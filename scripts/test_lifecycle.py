#!/usr/bin/env python3
"""Real Postgres acceptance and interruption tests; use a test-failpoints build.
Run from the repository root. Only fresh, private, generated test stores are used.
"""
import fcntl
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

PG = Path(os.environ.get('PG_BIN', '/opt/homebrew/opt/postgresql@17/bin'))
BIN = Path(os.environ.get('FORKLIFT_BIN', 'target/debug/forklift')).resolve()
Path('benchmark-data').mkdir(exist_ok=True)
ROOT = Path(tempfile.mkdtemp(prefix='lifecycle-', dir='benchmark-data')).resolve()
HOME = ROOT / 'store with spaces'
records = []


def cli(*args, code=None, fault=None, home=HOME):
    env = os.environ.copy()
    if fault:
        env['FORKLIFT_FAILPOINT'] = fault
    else:
        env.pop('FORKLIFT_FAILPOINT', None)
    start = time.monotonic()
    p = subprocess.run([str(BIN), '--home', str(home), '--pg-bin', str(PG), '--json', *args],
                       capture_output=True, text=True, env=env, timeout=90)
    if fault:
        assert p.returncode == 99, (args, p.stdout, p.stderr)
        records.append({'args': list(args), 'fault': fault, 'exit': 99})
        return None
    try:
        obj = json.loads(p.stdout)
    except ValueError:
        raise AssertionError((args, p.returncode, p.stdout, p.stderr))
    if code:
        assert not obj['ok'] and obj['error']['code'] == code, (args, obj)
    else:
        assert p.returncode == 0 and obj['ok'], (args, obj, p.stderr)
    records.append({'args': list(args), 'elapsed_seconds': time.monotonic()-start, 'response': obj})
    return obj.get('result')


def connection(name):
    return cli('connection', name)['connection']


def sql(name, statement):
    return subprocess.check_output([str(PG/'psql'), connection(name), '-XAt', '-v',
                                    'ON_ERROR_STOP=1', '-c', statement], text=True).strip()


def digest(name):
    return sql(name, "SELECT count(*) || ':' || md5(string_agg(row_to_json(a)::text, '' ORDER BY id)) FROM accounts a")


def cat():
    return json.loads((HOME/'catalog.json').read_text())


def data(name):
    return HOME/'generations'/cat()['branches'][name]['head']['id']/'data'


def checkpoint_state(result, name):
    return next(c['state'] for c in result['checkpoints'] if c['name'] == name)


try:
    print('create, seed, checkpoint, branch, conflicting edits', flush=True)
    cli('init', 'demo')
    cli('connection', 'demo', code='not_running')
    cli('start', 'demo')
    subprocess.run([str(PG/'psql'), connection('demo'), '-X', '-v', 'ON_ERROR_STOP=1',
                    '-v', 'rows=1000', '-f', 'fixtures/seed.sql'], check=True, capture_output=True)
    original = digest('demo')
    cli('checkpoint', 'demo', '--name', 'seed')
    assert digest('demo') == original
    cli('branch', 'demo', '--from', 'seed', '--name', 'a')
    cli('branch', 'demo', '--from', 'seed', '--name', 'b', '--copy-mode', 'full')
    cli('start', 'a'); cli('start', 'b')
    for name, script in [('a','fixtures/edit.sql'), ('b','fixtures/conflict.sql')]:
        subprocess.run([str(PG/'psql'), connection(name), '-X', '-v', 'ON_ERROR_STOP=1',
                        '-f', script], check=True, capture_output=True)
    edited_a, edited_b = digest('a'), digest('b')
    assert edited_a != original and edited_b != original and digest('demo') == original
    cli('stop', 'a'); cli('start', 'a')
    assert digest('a') == edited_a
    cli('reset', 'a', '--to', 'seed', code='confirmation_required')
    assert digest('a') == edited_a
    cli('reset', 'a', '--to', 'seed', '--yes')
    assert digest('a') == original and digest('b') == edited_b and digest('demo') == original
    cli('branch', '--from', 'seed', '--name', 'a', code='already_exists')
    cli('init', '../escape', code='invalid_name')
    cli('branch', 'b', '--from', 'seed', '--name', 'wrong', code='ancestry')
    cli('stop', 'a'); cli('reset', 'a', '--to', 'seed', '--yes')
    cli('connection', 'a', code='not_running'); cli('start', 'a')

    print('successful import and failed-import rollback', flush=True)
    archive = ROOT/'seed.dump'
    subprocess.run([str(PG/'pg_dump'), connection('demo'), '-Fc', '--compress=0', '-f', str(archive)], check=True)
    cli('init', 'imported'); cli('import', 'imported', '--yes', '--dump', str(archive)); cli('start', 'imported')
    assert digest('imported') == original
    corrupt = ROOT/'bad.dump'
    payload = archive.read_bytes()
    needle = b'1\t7919\t1\t'
    assert needle in payload
    corrupt.write_bytes(payload.replace(needle, b'x\t7919\t1\t', 1))
    cli('import', 'imported', '--dump', str(archive), code='confirmation_required')
    head_before = cat()['branches']['imported']['head']['id']
    cli('import', 'imported', '--yes', '--dump', str(corrupt), code='postgres')
    assert cat()['branches']['imported']['head']['id'] == head_before
    assert digest('imported') == original

    print('lock, unsafe path and process identity protection', flush=True)
    with (HOME/'lock').open('r+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        cli('list', code='locked')
        fcntl.flock(lock, fcntl.LOCK_UN)
    cli('stop', 'a')
    cli('nonsense', code='usage')
    unowned = ROOT/'unowned'; unowned.mkdir(mode=0o700)
    (unowned/'sentinel').write_text('untouched')
    cli('init', 'nope', home=unowned, code='io')
    assert list(unowned.iterdir()) == [unowned/'sentinel']
    external = ROOT/'external'; external.mkdir()
    outside = external/'sentinel'; outside.write_text('untouched')
    link = data('a')/'unsafe-link'; link.symlink_to(external, target_is_directory=True)
    cli('start', 'a', code='unsafe_path')
    assert outside.read_text() == 'untouched'
    link.unlink()
    hardlink = data('a')/'hard-link'
    os.link(outside, hardlink)
    cli('start', 'a', code='unsafe_path')
    assert outside.read_text() == 'untouched'
    hardlink.unlink(); cli('start', 'a')
    catalog_path = HOME/'catalog.json'
    saved = catalog_path.read_bytes()
    bad = json.loads(saved)
    bad['branches']['a']['head']['process']['pg_start'] -= 100
    catalog_path.write_text(json.dumps(bad))
    cli('stop', 'a', code='process_identity')
    catalog_path.write_bytes(saved)
    assert digest('a') == original
    bad = json.loads(saved)
    bad['branches']['a']['head']['id'] = bad['branches']['b']['head']['id']
    catalog_path.write_text(json.dumps(bad))
    cli('list', code='catalog')
    catalog_path.write_bytes(saved)

    print('interrupted start, checkpoint and reset recovery', flush=True)
    cli('init', 'unfinished', fault='init_files_ready')
    assert next(b['state'] for b in cli('list')['branches'] if b['name']=='unfinished') == 'failed'
    cli('start', 'unfinished', code='invalid_state')
    cli('stop', 'unfinished', code='invalid_state')
    cli('stop', 'a')
    cli('start', 'a', fault='start_launched')
    cli('list')
    assert digest('a') == original
    cli('checkpoint', 'a', '--name', 'interrupted', fault='checkpoint_stopped')
    assert checkpoint_state(cli('list'), 'interrupted') == 'failed'
    assert digest('a') == original
    cli('branch', '--from', 'interrupted', '--name', 'unusable', code='invalid_state')
    sql('a', 'UPDATE accounts SET balance=balance+1 WHERE id=1')
    before_reset = digest('a')
    cli('reset', 'a', '--to', 'seed', '--yes', fault='replacement_ready')
    cli('list')
    assert digest('a') == before_reset  # Catalog never switched.
    cli('reset', 'a', '--to', 'seed', '--yes', fault='replacement_published')
    cli('list')
    assert digest('a') == original  # Durable pointer switched, restart reconciled.
    cli('import', 'imported', '--yes', '--dump', str(archive), fault='replacement_ready')
    cli('list')
    assert digest('imported') == original

    print('external stop reconciliation and postgres crash recovery', flush=True)
    subprocess.run([str(PG/'pg_ctl'), '-D', str(data('a')), '-m', 'fast', '-w', 'stop'], check=True, capture_output=True)
    state = cli('list')
    assert next(b['state'] for b in state['branches'] if b['name']=='a') == 'stopped'
    cli('start', 'a')
    sql('a', 'UPDATE accounts SET balance=balance+3 WHERE id=1')
    committed = digest('a')
    # Immediate shutdown exercises WAL recovery without leaving orphan children.
    subprocess.run([str(PG/'pg_ctl'), '-D', str(data('a')), '-m', 'immediate', '-w', 'stop'], check=True, capture_output=True)
    cli('start', 'a')
    assert digest('a') == committed
    for name in ('a', 'b', 'demo', 'imported'):
        cli('stop', name)
    assert not list(HOME.rglob('postmaster.pid'))
    (ROOT/'SUCCESS').write_text('all lifecycle acceptance and interruption checks passed\n')
    print(f'PASS: {ROOT}', flush=True)
finally:
    # Best effort cleanup is restricted to known test generations with matching PGDATA.
    if (HOME/'catalog.json').exists():
        for pgdata in (HOME/'generations').glob('*/data'):
            pidfile = pgdata/'postmaster.pid'
            if pidfile.exists() and pidfile.read_text().splitlines()[1] == str(pgdata):
                subprocess.run([str(PG/'pg_ctl'), '-D', str(pgdata), '-m', 'fast', '-w', 'stop'], capture_output=True)
    (ROOT/'test-results.json').write_text(json.dumps(records, indent=2)+'\n')
