#!/usr/bin/env python3
"""Milestone-three real Postgres, deduplication, corruption and interruption tests."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import time

PG = Path(os.environ.get('PG_BIN', '/opt/homebrew/opt/postgresql@17/bin'))
BIN = Path(os.environ.get('FORKLIFT_BIN', 'target/debug/forklift')).resolve()
Path('benchmark-data').mkdir(exist_ok=True)
ROOT = Path(tempfile.mkdtemp(prefix='objects-', dir='benchmark-data')).resolve()
SOURCE, TARGET = ROOT/'source', ROOT/'target'
records = []


def cli(*args, home=SOURCE, code=None, fault=None):
    env = os.environ.copy()
    env.pop('FORKLIFT_FAILPOINT', None)
    if fault:
        env['FORKLIFT_FAILPOINT'] = fault
    t = time.monotonic()
    p = subprocess.run([str(BIN), '--home', str(home), '--pg-bin', str(PG), '--json', *args],
                       env=env, text=True, capture_output=True, timeout=180)
    if fault:
        assert p.returncode == 99, (args, p.stdout, p.stderr)
        records.append({'args': args, 'home': str(home), 'fault': fault, 'exit': 99})
        return None
    try:
        response = json.loads(p.stdout)
    except ValueError:
        raise AssertionError((args, p.returncode, p.stdout, p.stderr))
    if code:
        assert not response['ok'] and response['error']['code'] in code.split('|'), (args, response)
    else:
        assert p.returncode == 0 and response['ok'], (args, response, p.stderr)
    records.append({'args': args, 'home': str(home), 'elapsed_seconds': time.monotonic()-t, 'response': response})
    return response.get('result')


def sql(name, query, home=SOURCE, database='postgres'):
    connection = cli('connection', name, home=home)['connection'].replace('dbname=postgres', 'dbname='+database)
    return subprocess.check_output([str(PG/'psql'), connection, '-XAt', '-v', 'ON_ERROR_STOP=1', '-c', query], text=True).strip()


def digest(name, home=SOURCE):
    data = sql(name, "SELECT count(*) || ':' || md5(string_agg(md5(row_to_json(a)::text), '' ORDER BY id)) FROM accounts a", home)
    schema = sql(name, "SELECT md5(string_agg(v,'|' ORDER BY v)) FROM (SELECT column_name||':'||data_type||':'||coalesce(column_default,'') v FROM information_schema.columns WHERE table_schema='public' AND table_name='accounts' UNION ALL SELECT indexdef FROM pg_indexes WHERE schemaname='public') s", home)
    extra = sql(name, 'SELECT string_agg(value,\',\' ORDER BY id) FROM extra_data', home, 'extra')
    return {'data': data, 'schema': schema, 'extra_database': extra}


def state(result, group, name):
    return next(x['state'] for x in result[group] if x['name']==name)


def bundle_case(name):
    dest = ROOT/name
    shutil.copytree(ROOT/'seed.forklift', dest)
    return dest


def rewrite_manifest(path, change):
    m = json.loads((path/'manifest.json').read_bytes())
    change(m)
    raw = json.dumps(m, separators=(',', ':')).encode()
    (path/'manifest.json').write_bytes(raw)
    (path/'manifest.sha256').write_text(hashlib.sha256(raw).hexdigest())


def reject_manifest(name, change, code='invalid_manifest'):
    path = bundle_case(name)
    rewrite_manifest(path, change)
    cli('restore', str(path), '--name', name, home=TARGET, code=code)


try:
    print('100,000 rows, whole-cluster checkpoint and deduplication', flush=True)
    cli('init', 'demo'); cli('start', 'demo')
    connection = cli('connection', 'demo')['connection']
    subprocess.run([str(PG/'psql'), connection, '-X', '-v', 'ON_ERROR_STOP=1', '-v', 'rows=100000', '-f', 'fixtures/seed.sql'], check=True, capture_output=True)
    sql('demo', 'CREATE DATABASE extra')
    sql('demo', "CREATE TABLE extra_data(id integer PRIMARY KEY,value text); INSERT INTO extra_data VALUES(1,'whole-cluster')", database='extra')
    expected = digest('demo')
    first = cli('checkpoint', 'demo', '--name', 'seed')
    second = cli('checkpoint', 'demo', '--name', 'same')
    reused = next(e['metrics'] for e in second['events'] if e['stage']=='object_capture')
    assert reused['reused_existing_bytes'] > reused['scanned_bytes'] * .80, reused
    sql('demo', 'ALTER TABLE accounts ADD COLUMN experiment text DEFAULT \'edited\'; UPDATE accounts SET balance=balance+7 WHERE id%100=0')
    changed = digest('demo')
    third = cli('checkpoint', 'demo', '--name', 'changed')
    source_stats = cli('stats')
    assert source_stats['total']['apparent_bytes'] >= source_stats['generations']['apparent_bytes'] + source_stats['objects']['apparent_bytes']
    cli('export', 'seed', '--output', str(ROOT/'seed.forklift'))
    manifest = json.loads((ROOT/'seed.forklift'/'manifest.json').read_bytes())
    assert set(p.stem for p in (ROOT/'seed.forklift'/'packs').iterdir()) == {v['pack'] for v in manifest['chunks'].values()}
    before_export = (ROOT/'seed.forklift'/'manifest.json').read_bytes()
    cli('export', 'changed', '--output', str(ROOT/'seed.forklift'), code='already_exists')
    assert before_export == (ROOT/'seed.forklift'/'manifest.json').read_bytes()

    print('restore with the entire original store unavailable', flush=True)
    cli('stop', 'demo')
    offline = ROOT/'source-offline'
    SOURCE.rename(offline)
    try:
        cli('restore', str(ROOT/'seed.forklift'), '--name', 'restored', home=TARGET)
        cli('start', 'restored', home=TARGET)
        assert digest('restored', TARGET) == expected
    finally:
        offline.rename(SOURCE)
    cli('restore', str(ROOT/'seed.forklift'), '--name', 'restored', home=TARGET, code='already_exists')
    cli('stop', 'restored', home=TARGET)
    cli('checkpoint', 'demo', '--name', 'parked')
    assert state(cli('list'), 'branches', 'demo') == 'stopped'
    cli('branch', '--from', 'seed', '--name', 'experiment')
    cli('start', 'experiment')
    assert digest('experiment') == expected
    cli('reset', 'experiment', '--to', 'changed', '--yes')
    assert digest('experiment') == changed

    print('manifest validation, missing/corrupt packs and unsafe links', flush=True)
    bad = bundle_case('bad-manifest')
    (bad/'manifest.json').write_bytes((bad/'manifest.json').read_bytes()+b' ')
    cli('restore', str(bad), '--name', 'bad-manifest', home=TARGET, code='integrity')
    reject_manifest('traversal', lambda m: m['files'][0].update(path='../escaped'))
    reject_manifest('absolute', lambda m: m['files'][0].update(path='/tmp/escaped'))
    reject_manifest('case-alias', lambda m: m['files'].append(dict(m['files'][0], path=m['files'][0]['path'].lower())))
    reject_manifest('bad-length', lambda m: m['files'][0].update(length=m['files'][0]['length']+1))
    reject_manifest('bad-offset', lambda m: next(iter(m['chunks'].values())).update(offset=2**64-1))
    reject_manifest('missing-parent', lambda m: m['directories'].remove('global'))
    reject_manifest('incompatible', lambda m: m['compatibility'].update(postgres_major=18), 'incompatible')
    reject_manifest('wrong-platform', lambda m: m['compatibility'].update(platform='linux-x86_64'), 'incompatible')
    reject_manifest('wrong-build', lambda m: m['compatibility'].update(build_configuration='different'), 'incompatible')
    reject_manifest('extension', lambda m: m['extensions'][0].update(name='custom_extension'), 'incompatible')
    reject_manifest('wrong-file-hash', lambda m: m['files'][0].update(sha256='0'*64), 'integrity')
    first_chunk = manifest['files'][0]['chunks'][0]
    packname = manifest['chunks'][first_chunk]['pack']+'.pack'
    bad = bundle_case('missing-pack'); (bad/'packs'/packname).unlink()
    cli('restore', str(bad), '--name', 'missing-pack', home=TARGET, code='missing_object')
    bad = bundle_case('corrupt-pack'); p = bad/'packs'/packname
    raw = bytearray(p.read_bytes()); raw[-8] ^= 0xff; p.write_bytes(raw)
    cli('restore', str(bad), '--name', 'corrupt-pack', home=TARGET, code='integrity')
    bad = bundle_case('oversized-pack'); p = bad/'packs'/packname
    raw = bytearray(p.read_bytes()); raw[8:16] = struct.pack('<Q', 2**63); p.write_bytes(raw)
    cli('restore', str(bad), '--name', 'oversized-pack', home=TARGET, code='integrity')
    bad = bundle_case('linked-pack'); p = bad/'packs'/packname; p.unlink(); p.symlink_to(ROOT/'seed.forklift'/'packs'/packname)
    cli('restore', str(bad), '--name', 'linked-pack', home=TARGET, code='io|unsafe_path')
    bad = bundle_case('linked-directory'); shutil.rmtree(bad/'packs'); (bad/'packs').symlink_to(ROOT/'seed.forklift'/'packs', target_is_directory=True)
    cli('restore', str(bad), '--name', 'linked-directory', home=TARGET, code='unsafe_path')
    assert not (ROOT/'escaped').exists()
    cli('start', 'restored', home=TARGET)
    assert digest('restored', TARGET) == expected
    cli('stop', 'restored', home=TARGET)

    print('interrupted object writes, manifest publication and source recovery', flush=True)
    cli('start', 'demo')
    for fault in ('object_during_write', 'object_written', 'manifest_before_publish', 'manifest_published'):
        sql('demo', 'UPDATE accounts SET balance=balance+1 WHERE id=1')
        name = fault.replace('_', '-')
        cli('checkpoint', 'demo', '--name', name, fault=fault)
        result = cli('list')
        assert state(result,'checkpoints',name)=='failed' and state(result,'branches','demo')=='running'
        cli('branch', '--from', name, '--name', 'unusable-'+name, code='invalid_state')
    cli('stop', 'demo')
    cli('checkpoint', 'demo', '--name', 'interrupted-parked', fault='start_launched')
    assert state(cli('list'),'branches','demo')=='stopped'
    assert state(cli('list'),'checkpoints','interrupted-parked')=='failed'

    print('interrupted exports/restores, previous checkpoints still usable', flush=True)
    for fault in ('export_pack_written', 'export_before_publish'):
        out = ROOT/(fault+'.forklift')
        cli('export', 'seed', '--output', str(out), fault=fault)
        assert not out.exists()
    for fault in ('restore_during_write', 'restore_before_publish'):
        name = fault.replace('_', '-')
        cli('restore', str(ROOT/'seed.forklift'), '--name', name, home=TARGET, fault=fault)
        assert state(cli('list',home=TARGET),'branches',name)=='failed'
        cli('start', name, home=TARGET, code='invalid_state')
    cli('export', 'seed', '--output', str(ROOT/'still-valid.forklift'))
    cli('restore', str(ROOT/'still-valid.forklift'), '--name', 'after-interruptions', home=TARGET)
    cli('start', 'after-interruptions', home=TARGET)
    assert digest('after-interruptions',TARGET)==expected
    cli('stop', 'after-interruptions', home=TARGET)
    cli('stop', 'experiment')
    final_source_stats = cli('stats'); final_target_stats = cli('stats',home=TARGET)
    assert final_source_stats['objects']['apparent_bytes'] >= source_stats['objects']['apparent_bytes']
    assert not list(SOURCE.rglob('postmaster.pid')) and not list(TARGET.rglob('postmaster.pid'))
    report = {'expected':expected,'changed':changed,'first_capture':first['events'],'second_capture':second['events'],
              'changed_capture':third['events'],'initial_source_stats':source_stats,'final_source_stats':final_source_stats,
              'final_target_stats':final_target_stats,'export_apparent_bytes':sum(p.stat().st_size for p in (ROOT/'seed.forklift').rglob('*') if p.is_file()),
              'retained_export_staging_apparent_bytes':sum(p.stat().st_size for d in ROOT.glob('.forklift-export-*') for p in d.rglob('*') if p.is_file())}
    (ROOT/'summary.json').write_text(json.dumps(report,indent=2)+'\n')
    (ROOT/'SUCCESS').write_text('all object checkpoint, portability, integrity and interruption checks passed\n')
    print('PASS: '+str(ROOT), flush=True)
finally:
    for store in (SOURCE,TARGET,ROOT/'source-offline'):
        if store.exists():
            for data in (store/'generations').glob('*/data'):
                pidfile = data/'postmaster.pid'
                if pidfile.exists() and pidfile.read_text().splitlines()[1] == str(data):
                    subprocess.run([str(PG/'pg_ctl'), '-D', str(data), '-m', 'fast', '-w', 'stop'], capture_output=True)
    (ROOT/'test-results.json').write_text(json.dumps(records,indent=2)+'\n')
