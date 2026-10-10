#!/usr/bin/env python3
"""Real PostgreSQL extension values, functions and fail-closed portability."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

PG = Path(os.environ.get('PG_BIN', '/opt/homebrew/opt/postgresql@17/bin'))
BIN = Path(os.environ.get('FORKLIFT_BIN', 'target/debug/forklift')).resolve()
Path('benchmark-data').mkdir(exist_ok=True)
ROOT = Path(tempfile.mkdtemp(prefix='extensions-', dir='benchmark-data')).resolve()
SOURCE, TARGET = ROOT/'source', ROOT/'target'


def cli(*args, home=SOURCE, code=None):
    env = os.environ.copy()
    env.pop('FORKLIFT_FAILPOINT', None)
    p = subprocess.run([str(BIN), '--home', str(home), '--pg-bin', str(PG), '--json', *args],
                       env=env, text=True, capture_output=True, timeout=180)
    response = json.loads(p.stdout)
    if code:
        assert not response['ok'] and response['error']['code'] == code, (args, response)
    else:
        assert p.returncode == 0 and response['ok'], (args, response, p.stderr)
    return response.get('result')


def sql(name, query, home=SOURCE, database='postgres'):
    connection = cli('connection', name, home=home)['connection']
    return subprocess.check_output([str(PG/'psql'), connection.replace('dbname=postgres', 'dbname='+database),
                                    '-XAt', '-v', 'ON_ERROR_STOP=1', '-c', query], text=True).strip()


def verify(name, home=SOURCE):
    assert sql(name, "SELECT label = 'mixed', attrs -> 'key', id = uuid_generate_v5(uuid_ns_dns(), 'forklift') FROM extension_values", home) == 't|value|t'
    assert sql(name, "SELECT uuid_generate_v4() IS NOT NULL", home) == 't'
    assert sql(name, "SELECT similarity('forklift', 'forklift') = 1", home) == 't'
    assert sql(name, "SELECT value = 'extra' FROM extra_values", home, 'extra') == 't'


try:
    cli('init', 'demo'); cli('start', 'demo')
    sql('demo', 'CREATE EXTENSION citext; CREATE EXTENSION hstore; CREATE EXTENSION "uuid-ossp"; CREATE EXTENSION pg_trgm; '
        'CREATE TABLE extension_values(label citext PRIMARY KEY, attrs hstore, id uuid); '
        "INSERT INTO extension_values VALUES ('MiXeD', 'key=>value', uuid_generate_v5(uuid_ns_dns(), 'forklift'))")
    sql('demo', 'CREATE DATABASE extra')
    sql('demo', "CREATE EXTENSION citext; CREATE TABLE extra_values(value citext); INSERT INTO extra_values VALUES ('Extra')", database='extra')
    verify('demo')
    cli('checkpoint', 'demo', '--name', 'seed')
    cli('branch', '--from', 'seed', '--name', 'branch'); cli('start', 'branch'); verify('branch'); cli('stop', 'branch')
    sql('demo', "UPDATE extension_values SET attrs='key=>changed'")
    cli('reset', 'demo', '--to', 'seed', '--yes'); verify('demo')
    cli('export', 'seed', '--output', str(ROOT/'seed.forklift'))
    cli('restore', str(ROOT/'seed.forklift'), '--name', 'restored', home=TARGET)
    cli('start', 'restored', home=TARGET); verify('restored', TARGET); cli('stop', 'restored', home=TARGET)
    manifest = json.loads((ROOT/'seed.forklift/manifest.json').read_text())
    assert {e['name'] for e in manifest['extensions']} == {'plpgsql', 'citext', 'hstore', 'uuid-ossp', 'pg_trgm'}
    for label, change in (
        ('missing-fingerprint', lambda e: e.pop('installation_sha256')),
        ('changed-installation', lambda e: e.update(installation_sha256='0'*64)),
        ('unsupported-version', lambda e: e.update(version='0.0')),
    ):
        bundle = ROOT/label
        shutil.copytree(ROOT/'seed.forklift', bundle)
        m = json.loads((bundle/'manifest.json').read_text())
        change(next(e for e in m['extensions'] if e['name']=='citext'))
        raw = json.dumps(m, separators=(',', ':')).encode()
        (bundle/'manifest.json').write_bytes(raw)
        (bundle/'manifest.sha256').write_text(hashlib.sha256(raw).hexdigest())
        cli('restore', str(bundle), '--name', label, home=TARGET, code='incompatible')
    sql('demo', 'CREATE EXTENSION pgcrypto')
    cli('checkpoint', 'demo', '--name', 'unsupported', code='incompatible')
    verify('demo')
    sql('demo', 'DROP EXTENSION pgcrypto')
    # A supported name cannot hide a C function pointing at a different library.
    sql('demo', "UPDATE pg_proc SET probin='$libdir/missing' WHERE proname='citext_eq'")
    cli('checkpoint', 'demo', '--name', 'bad-library', code='incompatible')
    sql('demo', "UPDATE pg_proc SET probin='$libdir/citext' WHERE proname='citext_eq'")
    verify('demo')
    cli('stop', 'demo')
    cli('checkpoint', 'demo', '--name', 'stopped-seed')
    assert next(b for b in cli('list')['branches'] if b['name']=='demo')['state']=='stopped'
    (ROOT/'SUCCESS').write_text('supported extension branching, reset, export/restore and rejection checks passed\n')
    print('PASS: '+str(ROOT), flush=True)
finally:
    for store in (SOURCE, TARGET):
        for data in (store/'generations').glob('*/data'):
            pidfile = data/'postmaster.pid'
            if pidfile.exists() and pidfile.read_text().splitlines()[1] == str(data):
                subprocess.run([str(PG/'pg_ctl'), '-D', str(data), '-m', 'fast', '-w', 'stop'], capture_output=True)
