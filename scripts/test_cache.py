#!/usr/bin/env python3
"""Real PostgreSQL cache integrity, crash recovery and running-admission checks."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

PG = Path(os.environ.get('PG_BIN', '/opt/homebrew/opt/postgresql@17/bin'))
BIN = Path(os.environ.get('FORKLIFT_BIN', 'target/release/forklift')).resolve()
ROOT = Path(tempfile.mkdtemp(prefix='cache-', dir='benchmark-data')).resolve()
HOME = ROOT/'store'
records = []


def cli(*args, code=None, fault=None):
    env = os.environ.copy(); env.pop('FORKLIFT_FAILPOINT', None)
    if fault: env['FORKLIFT_FAILPOINT'] = fault
    t = time.monotonic()
    p = subprocess.run([str(BIN), '--home', str(HOME), '--pg-bin', str(PG), '--json', *args],
                       env=env, text=True, capture_output=True, timeout=180)
    if fault:
        assert p.returncode == 99, (args, p.stdout, p.stderr)
        records.append({'args': args, 'fault': fault, 'exit': 99}); return
    obj = json.loads(p.stdout)
    if code: assert not obj['ok'] and obj['error']['code'] == code, (args, obj)
    else: assert p.returncode == 0 and obj['ok'], (args, obj, p.stderr)
    records.append({'args': args, 'elapsed_seconds': time.monotonic()-t, 'response': obj})
    return obj.get('result')


def cat(): return json.loads((HOME/'catalog.json').read_text())
def save(c): (HOME/'catalog.json').write_text(json.dumps(c))
def sql(name, query):
    conn = cli('connection', name)['connection']
    return subprocess.check_output([str(PG/'psql'), conn, '-XAt', '-v', 'ON_ERROR_STOP=1', '-c', query], text=True).strip()
def stages(r): return [e['stage'] for e in r['events']]
def value(name): return sql(name, 'SELECT value FROM retained')
def fork(name, **kw): return cli('branch', '--from', 'seed', '--name', name, **kw)
def cache(): return HOME/'cache'/cat()['checkpoints']['seed']['manifest']
def signature():
    return {str(p.relative_to(cache())): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in cache().rglob('*') if p.is_file()}

try:
    print('cache build, verified reuse and isolation', flush=True)
    assert cli('init', 'demo')['max_running'] == 3
    cli('start', 'demo')
    sql('demo', "CREATE TABLE retained(value text); INSERT INTO retained VALUES('seed')")
    cli('checkpoint', 'demo', '--name', 'seed'); cli('stop', 'demo')
    # Simulate interruption before and after atomic cache publication.
    fork('before', fault='cache_before_publish'); cli('list')
    assert not cache().exists()
    fork('published', fault='cache_published'); cli('list')
    assert cache().exists() and list((HOME/'cache').glob('.building-*'))
    before = signature()
    assert 'cache_hit_verify' in stages(fork('a'))
    assert 'cache_full_copy_and_sync' in stages(cli('branch', '--from', 'seed', '--name', 'b', '--copy-mode', 'full'))
    assert 'object_materialization' in stages(cli('branch', '--from', 'seed', '--name', 'direct', '--no-cache'))
    cli('start', 'a'); cli('start', 'b'); cli('start', 'direct')
    sql('a', "UPDATE retained SET value='edited'; ALTER TABLE retained ADD COLUMN extra int")
    assert value('b') == value('direct') == 'seed'
    assert signature() == before
    cli('reset', 'a', '--to', 'seed', '--yes')
    assert value('a') == 'seed' and signature() == before
    cli('start', 'demo', code='running_limit')
    cli('config', '--max-running', '2', code='running_limit')
    cli('config', '--max-running', '0', code='invalid_limit')
    cli('config', '--max-running', '101', code='invalid_limit')
    cli('checkpoint', 'demo', '--name', 'no-slot', code='running_limit')
    assert cli('list')['active_clusters'] == 3

    print('temporary import admission and rollback', flush=True)
    archive = ROOT/'seed.dump'
    conn = cli('connection', 'b')['connection']
    subprocess.run([str(PG/'pg_dump'), conn, '-Fc', '-f', str(archive)], check=True)
    cli('import', 'demo', '--yes', '--dump', str(archive), code='running_limit')
    cli('import', 'a', '--yes', '--dump', str(archive), fault='import_admission_stopped')
    assert cli('list')['active_clusters'] == 3 and value('a') == 'seed'
    cli('import', 'a', '--yes', '--dump', str(archive))
    assert cli('list')['active_clusters'] == 3 and value('a') == 'seed'
    for name in ('a', 'b', 'direct'): cli('stop', name)

    print('corrupt cache rebuild; unsafe cache fails closed', flush=True)
    version = cache()/'data/PG_VERSION'; version.write_text('18\n')
    r = fork('rebuilt')
    assert 'cache_quarantined' in stages(r) and 'cache_build_verify_publish' in stages(r)
    assert list((HOME/'cache').glob('.invalid-*'))
    cli('start', 'rebuilt'); assert value('rebuilt') == 'seed'; cli('stop', 'rebuilt')
    sentinel = ROOT/'sentinel'; sentinel.write_text('untouched')
    link = cache()/'data/unsafe'; link.symlink_to(sentinel)
    fork('linked', code='unsafe_path'); link.unlink()
    marker = cache()/'entry.json'; saved = marker.read_bytes()
    bad = json.loads(saved); bad['store_id'] = 'wrong'; marker.write_text(json.dumps(bad))
    fork('owner', code='cache_identity'); marker.write_bytes(saved)
    pid = cache()/'data/postmaster.pid'; pid.write_text(str(os.getpid()))
    fork('pid', code='cache_running'); assert pid.exists(); pid.unlink()
    assert sentinel.read_text() == 'untouched'
    fork('copied', fault='cache_branch_copied')
    r = cli('list'); assert next(b['state'] for b in r['branches'] if b['name']=='copied') == 'failed'
    assert signature() == before
    extra = cache()/'data/unexpected'; extra.write_text('extra')
    assert 'cache_quarantined' in stages(fork('extra-path'))
    # Cached bytes are verified independently; export/direct reconstruction must
    # still fail if the underlying repository is incomplete.
    pack = next((HOME/'objects/packs').glob('*.pack'))
    saved_pack = pack.read_bytes(); pack.unlink()
    assert 'cache_hit_verify' in stages(fork('without-pack'))
    cli('branch', '--from', 'seed', '--name', 'missing-pack', '--no-cache', code='missing_object')
    cli('export', 'seed', '--output', str(ROOT/'missing.forklift'), code='missing_object')
    pack.write_bytes(saved_pack)

    print('deferred recovery remains manageable; explicit stop cancels resume', flush=True)
    cli('config', '--max-running', '1'); cli('start', 'a')
    c = cat(); c['branches']['b']['resume'] = True; save(c)
    assert 'resume_deferred_capacity' in stages(cli('list'))
    cli('stop', 'a')
    assert cli('list')['active_clusters'] == 1 and value('b') == 'seed'
    c = cat(); c['branches']['a']['resume'] = True; save(c)
    cli('stop', 'a'); cli('stop', 'b')
    assert cli('list')['active_clusters'] == 0 and not cat()['branches']['a']['resume']
    cli('config', '--max-running', '2'); cli('start', 'a'); cli('start', 'b')
    assert cli('list')['active_clusters'] == 2
    cli('stop', 'a'); cli('stop', 'b')
    assert all(b['activity'] in ('parked','unavailable') for b in cli('list')['branches'])
    assert not list(HOME.rglob('postmaster.pid'))
    # Real format-2 layout shares this schema minus the new cap; migration supplies default.
    c = cat(); c['format'] = 2; del c['max_running']; save(c)
    assert cli('list')['max_running'] == 3 and cat()['format'] == 3
    assert cli('stats')['cache']['apparent_bytes'] > 0
    (ROOT/'SUCCESS').write_text('cache integrity, interruption, capacity and recovery checks passed\n')
    print('PASS: '+str(ROOT), flush=True)
finally:
    for data in (HOME/'generations').glob('*/data'):
        pid = data/'postmaster.pid'
        if pid.exists() and pid.read_text().splitlines()[1] == str(data):
            subprocess.run([str(PG/'pg_ctl'), '-D', str(data), '-m', 'fast', '-w', 'stop'], capture_output=True)
    (ROOT/'test-results.json').write_text(json.dumps(records, indent=2)+'\n')
