#!/usr/bin/env python3
"""Real PostgreSQL named-database, SCRAM TCP and preflight regression."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
from urllib.parse import urlparse

REPO = Path(__file__).resolve().parents[1]
BIN = Path(os.environ.get('FORKLIFT_BIN', REPO/'target/debug/forklift')).resolve()
PG = Path(os.environ.get('PG_BIN', '/opt/homebrew/opt/postgresql@17/bin'))
(REPO/'benchmark-data').mkdir(exist_ok=True)
ROOT = Path(tempfile.mkdtemp(prefix='onboarding-', dir=REPO/'benchmark-data'))
STORE = ROOT/'store'


def call(args, expected=0, env=None):
    p = subprocess.run([str(a) for a in args], capture_output=True, text=True, env=env, timeout=120)
    # Avoid including database URLs/passwords in test failures.
    assert p.returncode == expected, (p.returncode, p.stderr)
    return p.stdout.strip()


def db(*args):
    return json.loads(call([BIN, '--json', '--pg-bin', PG, '--home', STORE, *args]))['result']


def project(root, *args):
    return call([BIN, '--json', '--pg-bin', PG, 'project', '--root', root, *args])


def query(root, sql):
    return project(root, 'run', '--', 'python3', '-c',
        'import os,subprocess,sys; subprocess.run([os.environ["PG_BIN"]+"/psql",os.environ["DATABASE_URL"],"-XAt","-v","ON_ERROR_STOP=1","-c",sys.argv[1]],check=True)', sql)


try:
    db('init', 'seed'); db('start', 'seed')
    uri = db('connection', 'seed')['uri']
    call([PG/'psql', uri, '-X', '-v', 'ON_ERROR_STOP=1', '-c', 'CREATE DATABASE "app db"'])
    db('checkpoint', 'seed', '--name', 'baseline'); db('stop', 'seed')
    roots = []
    ports = []
    for number in range(2):
        root = ROOT/f'app{number}'; root.mkdir(); roots.append(root)
        call(['git', '-C', root, 'init'])
        (root/'src').mkdir(); (root/'src/main.txt').write_text('original')
        config = {'format': 1, 'source': ['src'], 'connection_env': 'DATABASE_URL', 'database': 'app db', 'tcp': True, 'commands': {}}
        (root/'.forklift.json').write_text(json.dumps(config))
        before = json.loads(project(root, 'doctor'))['result']
        assert not before['ready']
        project(root, 'configure', '--store', STORE, '--checkpoint', 'baseline')
        if number == 0:
            catalog_path = STORE/'catalog.json'
            catalog_bytes = catalog_path.read_bytes()
            catalog = json.loads(catalog_bytes)
            before_generations = set((STORE/'generations').iterdir())
            catalog['checkpoints']['baseline']['instance']['state'] = 'failed'
            try:
                catalog_path.write_text(json.dumps(catalog))
                report = json.loads(project(root, 'doctor'))['result']
                assert any(c['check'] == 'seed' and c['status'] == 'blocked' for c in report['checks'])
            finally:
                catalog_path.write_bytes(catalog_bytes)
            manifest = STORE/'objects/manifests'/f"{catalog['checkpoints']['baseline']['manifest']}.json"
            original_manifest = manifest.read_bytes()
            try:
                manifest.write_bytes(original_manifest + b'\n')
                report = json.loads(project(root, 'doctor'))['result']
                assert any(c['check'] == 'seed' and c['status'] == 'blocked' for c in report['checks'])
            finally:
                manifest.write_bytes(original_manifest)
            assert set((STORE/'generations').iterdir()) == before_generations
            assert not (root/'.git/forklift-id.json').exists()
            missing_pg = json.loads(call([BIN, '--json', '--pg-bin', ROOT/'missing-pg', 'project', '--root', root, 'doctor']))['result']
            assert not missing_pg['ready']
            assert any(c['check'] == 'postgres_installation' and c['status'] == 'blocked' for c in missing_pg['checks'])
            assert any(c['check'] == 'postgres_installation' and c['status'] == 'ok' for c in report['checks'])
        project(root, 'setup')
        doctor = json.loads(project(root, 'doctor'))['result']
        assert doctor['ready'], doctor
        assert query(root, 'SELECT current_database()') == 'app db'
        # Inherited libpq settings must not redirect the app away from its branch.
        poison = dict(os.environ, PGHOSTADDR='192.0.2.1', PGSERVICE='missing-service',
                      PGSERVICEFILE=str(ROOT/'missing-service.conf'), PGOPTIONS='invalid',
                      PGRST_DB_ANON_ROLE='local_api_role')
        child = ('import os,subprocess; '
                 'assert not any(k in os.environ for k in ["PGHOSTADDR","PGSERVICE","PGSERVICEFILE","PGOPTIONS"]); '
                 'assert os.environ["PGRST_DB_ANON_ROLE"] == "local_api_role"; '
                 'subprocess.run([os.environ["PG_BIN"]+"/psql",os.environ["DATABASE_URL"],'
                 '"-XAt","-v","ON_ERROR_STOP=1","-c","SELECT current_database()"],check=True)')
        assert call([BIN, '--json', '--pg-bin', PG, 'project', '--root', root,
                     'run', '--', 'python3', '-c', child], env=poison) == 'app db'
        assert query(root, 'SHOW listen_addresses') == '127.0.0.1'
        assert query(root, "SELECT current_setting('fsync') || ',' || current_setting('full_page_writes') || ',' || current_setting('synchronous_commit') || ',' || current_setting('data_checksums')") == 'on,on,on,on'
        uri = project(root, 'run', '--', 'python3', '-c', 'import os; print(os.environ["DATABASE_URL"])')
        parsed = urlparse(uri); ports.append(parsed.port)
        assert parsed.hostname == '127.0.0.1' and len(parsed.password) == 32
        env = {k:v for k,v in os.environ.items() if not k.startswith('PG')}
        env['PGPASSWORD'] = 'incorrect'; env['PGCONNECT_TIMEOUT'] = '3'
        call([PG/'psql', '-h', '127.0.0.1', '-p', parsed.port, '-U', 'forklift', '-d', 'app db', '-w', '-XAtc', 'SELECT 1'], expected=2, env=env)
        query(root, 'CREATE TABLE notes (value text); INSERT INTO notes VALUES (\'before\')')
        project(root, 'capture', 'saved')
        query(root, "UPDATE notes SET value='after'")
        project(root, 'restore', 'saved')
        assert query(root, 'SELECT value FROM notes') == 'before'
        assert json.loads(project(root, 'doctor'))['result']['ready']
    assert ports[0] != ports[1]
    # Preflight refuses a single oversized owned file before allocating a binding.
    bad = ROOT/'bad'; bad.mkdir(); call(['git', '-C', bad, 'init'])
    (bad/'large').write_bytes(b'')
    with (bad/'large').open('r+b') as f: f.truncate(16 * 1024 * 1024 + 1)
    (bad/'.forklift.json').write_text(json.dumps({'format':1, 'source':['large'], 'connection_env':'DATABASE_URL', 'commands':{}}))
    report = json.loads(project(bad, 'doctor'))['result']
    assert not report['ready']
    assert any(c['check'] == 'source' and c['status'] == 'blocked' for c in report['checks'])
    assert not (bad/'.git/forklift-id.json').exists()
    print('named database, authenticated TCP, isolated endpoints, doctor, capture and restore: passed')
finally:
    if STORE.exists():
        try:
            for branch in db('list')['branches']:
                db('stop', branch['name'])
        except Exception:
            pass
