#!/usr/bin/env python3
"""Opt-in real Booklist supabase-js/PostgREST replay, using synthetic local data only.
Requires BOOKLIST_ROOT, POSTGREST_BIN, PG_BIN (PG17 with vector 0.8.0).
Never reads Booklist dotenv files or connects to its hosted project.
"""
import base64
import hashlib
import hmac
import http.client
import http.server
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request

REPO = Path(__file__).resolve().parents[1]
BIN = Path(os.environ.get('FORKLIFT_BIN', REPO/'target/debug/forklift')).resolve()
PG = Path(os.environ.get('PG_BIN', '/opt/homebrew/opt/postgresql@17/bin')).resolve()
BOOKLIST = Path(os.environ['BOOKLIST_ROOT']).resolve()
POSTGREST = Path(os.environ['POSTGREST_BIN']).resolve()
ROOT = Path(tempfile.mkdtemp(prefix='booklist-', dir=REPO/'benchmark-data'))
APP, STORE = ROOT/'app', ROOT/'store'
APP.mkdir()
# Copy an explicit allowlist. In particular, no dotenv, cached production data, or Git metadata.
for name in ['scripts/dump-data.ts', 'utils/supabase/server.ts', 'tsconfig.json', 'package.json', 'package-lock.json']:
    target = APP/name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(BOOKLIST/name, target)
# Additional unchanged UI source for a real Next dev HTTP smoke (no generated data).
for directory in ['app', 'components', 'styles', 'config', 'types', 'utils']:
    for original in (BOOKLIST/directory).rglob('*'):
        if original.is_file() and not original.is_symlink() and original.suffix in ['.ts', '.tsx', '.js', '.css', '.json']:
            target = APP/original.relative_to(BOOKLIST)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(original, target)
for name in ['next.config.ts', 'next-env.d.ts', 'postcss.config.mjs', 'tailwind.config.ts', 'components.json', 'public/fonts/SpecialElite-Regular.ttf']:
    target = APP/name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(BOOKLIST/name, target)
# Dependencies are read from the existing checkout; no npm install or lifecycle scripts run.
(APP/'node_modules').symlink_to(BOOKLIST/'node_modules', target_is_directory=True)
# Keep the process environment free of inherited service keys or remote connection strings.
ENV = {k: v for k, v in os.environ.items() if k in ['PATH', 'HOME', 'TMPDIR', 'LANG', 'DYLD_LIBRARY_PATH']}
ENV['PG_BIN'] = str(PG)

def run(args, cwd=None, env=None):
    result = subprocess.run(list(map(str, args)), cwd=cwd, env=env or ENV,
                            capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, (args, result.stdout, result.stderr)
    return result.stdout

def db(*args):
    return json.loads(run([BIN, '--json', '--pg-bin', PG, '--home', STORE, *args]))['result']

def project(*args):
    return run([BIN, '--json', '--pg-bin', PG, 'project', '--root', APP, *args])

def sql(uri, query):
    return run([PG/'psql', uri, '-XAt', '-v', 'ON_ERROR_STOP=1', '-c', query]).strip()

def port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]

secret = 'forklift-booklist-local-test-secret-only'
def encode(value):
    return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b'=')
head = encode({'alg': 'HS256', 'typ': 'JWT'}) + b'.' + encode({'role': 'anon', 'exp': int(time.time())+3600})
token = (head+b'.'+base64.urlsafe_b64encode(hmac.new(secret.encode(), head, hashlib.sha256).digest()).rstrip(b'=')).decode()
rest_port = port()
# supabase-js appends /rest/v1; standalone PostgREST expects /rpc. This local-only
# path adapter forwards requests unchanged, including JWTs. It is not a Supabase gateway.
class Gateway(http.server.BaseHTTPRequestHandler):
    def forward(self):
        if not self.path.startswith('/rest/v1/'):
            self.send_error(404); return
        conn = http.client.HTTPConnection('127.0.0.1', rest_port, timeout=15)
        body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
        headers = {k:v for k,v in self.headers.items() if k.lower() not in ['host','connection']}
        try:
            conn.request(self.command, self.path[len('/rest/v1'):], body, headers)
            response = conn.getresponse()
            payload = response.read()
            self.send_response(response.status)
            for k,v in response.getheaders():
                if k.lower() not in ['transfer-encoding','connection','content-length']:
                    self.send_header(k,v)
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers(); self.wfile.write(payload)
        finally:
            conn.close()
    do_GET = do_POST = do_PATCH = do_DELETE = forward
    def log_message(self, *_): pass

gateway = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Gateway)
threading.Thread(target=gateway.serve_forever, daemon=True).start()
local_url = 'http://127.0.0.1:'+str(gateway.server_port)
api = None
next_server = None
succeeded = False
log = (ROOT/'postgrest.log').open('w')

def connection():
    return project('run', '--', sys.executable, '-c', 'import os; print(os.environ["DATABASE_URL"])').strip()

def start_api():
    global api
    parsed = urllib.parse.urlsplit(connection())
    auth_uri = urllib.parse.urlunsplit(parsed._replace(netloc='authenticator:local-booklist-only@'+parsed.netloc.split('@')[-1]))
    env = dict(ENV, PGRST_DB_URI=auth_uri, PGRST_DB_ANON_ROLE='anon', PGRST_DB_SCHEMAS='public',
               PGRST_DB_EXTRA_SEARCH_PATH='public,extensions', PGRST_SERVER_HOST='127.0.0.1',
               PGRST_SERVER_PORT=str(rest_port), PGRST_JWT_SECRET=secret)
    api = subprocess.Popen([str(POSTGREST)], env=env, stdout=log, stderr=log)
    for _ in range(100):
        if api.poll() is not None: raise RuntimeError((ROOT/'postgrest.log').read_text())
        try:
            with urllib.request.urlopen('http://127.0.0.1:'+str(rest_port)+'/books', timeout=1) as response:
                if response.status == 200: return
        except Exception: time.sleep(.1)
    raise RuntimeError('PostgREST did not become ready; see '+str(ROOT/'postgrest.log'))

def stop_api():
    global api
    if api:
        api.terminate(); api.wait(timeout=20); api = None

def dump():
    env = dict(ENV, NEXT_PUBLIC_SUPABASE_URL=local_url, NEXT_PUBLIC_SUPABASE_ANON_KEY=token)
    output = run([APP/'node_modules/.bin/tsx', 'scripts/dump-data.ts'], cwd=APP, env=env)
    assert 'Error' not in output, output # Booklist itself catches RPC failures and exits zero.
    result = {p.name:json.loads(p.read_text()) for p in (APP/'public/data').glob('*.json')}
    assert result['books-meta.json']['totalBooks'] == 2
    assert len(result['recommenders.json']) == 2
    assert len(result['books-essential.json'][0]['recommendations']) == 2
    assert len(result['books-extended.json'][0]['related_books']) == 1
    return result

def smoke_ui(expected):
    global next_server
    next_port = port()
    env = dict(ENV, NEXT_PUBLIC_SUPABASE_URL=local_url, NEXT_PUBLIC_SUPABASE_ANON_KEY=token, NEXT_TELEMETRY_DISABLED='1')
    with (ROOT/'next.log').open('w') as next_log:
        next_server = subprocess.Popen([str(APP/'node_modules/.bin/next'), 'dev', '--hostname', '127.0.0.1', '--port', str(next_port)], cwd=APP, env=env, stdout=next_log, stderr=next_log)
        url = 'http://127.0.0.1:'+str(next_port)+'/booklist'
        try:
            for _ in range(90):
                if next_server.poll() is not None: raise RuntimeError('Next stopped; see '+str(ROOT/'next.log'))
                try:
                    with urllib.request.urlopen(url, timeout=20) as response:
                        assert b'<!DOCTYPE html>' in response.read() or response.status == 200
                    break
                except (OSError, urllib.error.URLError): time.sleep(.2)
            else: raise RuntimeError('Next did not become ready; see '+str(ROOT/'next.log'))
            with urllib.request.urlopen(url+'/data/books-essential.json', timeout=10) as response:
                assert json.load(response) == expected['books-essential.json']
        finally:
            next_server.terminate(); next_server.wait(timeout=30); next_server = None

def request(path, method='GET', body=None):
    req = urllib.request.Request(local_url+'/rest/v1/'+path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={'Authorization':'Bearer '+token, 'Content-Type':'application/json','Prefer':'return=representation'})
    with urllib.request.urlopen(req, timeout=15) as response:
        data = response.read()
        return json.loads(data) if data else None

try:
    db('init', 'seed'); db('start', 'seed')
    uri = db('connection', 'seed')['uri']
    sql(uri, 'CREATE DATABASE booklist')
    parsed = urllib.parse.urlsplit(uri)
    book_uri = urllib.parse.urlunsplit(parsed._replace(path='/booklist'))
    run([PG/'psql', book_uri, '-X', '-v', 'ON_ERROR_STOP=1', '-f', REPO/'examples/booklist/schema.sql'])
    db('checkpoint', 'seed', '--name', 'booklist-seed'); db('stop', 'seed')
    (APP/'.forklift.json').write_text(json.dumps({'format':1,'source':['scripts/dump-data.ts','utils/supabase/server.ts','tsconfig.json'],
        'connection_env':'DATABASE_URL','database':'booklist','tcp':True,'commands':{}}))
    run(['git','init',APP]); run(['git','-C',APP,'add','.forklift.json','scripts','utils','tsconfig.json'])
    run(['git','-C',APP,'-c','user.name=fixture','-c','user.email=fixture@example.test','commit','-m','booklist fixture'])
    project('configure','--store',STORE,'--checkpoint','booklist-seed'); project('setup')
    assert json.loads(project('doctor'))['result']['ready']
    start_api()
    baseline = dump()
    assert request('people?id=eq.10000000-0000-0000-0000-000000000001','DELETE') == []
    assert len(request('people')) == 2 # DELETE denied by actual RLS policy, even with table grant.
    vector = [1]+[0]*1535
    semantic = request('rpc/semantic_search_books','POST', {'embedding_input':vector,'min_similarity':.8})
    assert len(semantic) == 2
    stop_api(); project('capture','baseline')
    source = APP/'scripts/dump-data.ts'
    source.write_text(source.read_text().replace('Fetching data from Supabase...', 'Local experiment...'))
    sql(connection(), "ALTER TABLE books ADD COLUMN experiment boolean DEFAULT true; UPDATE books SET title='Changed locally' WHERE id='00000000-0000-0000-0000-000000000001';")
    start_api(); assert dump() != baseline; stop_api()
    project('restore','baseline')
    assert 'Local experiment...' not in source.read_text()
    assert sql(connection(), "SELECT count(*) FROM information_schema.columns WHERE table_name='books' AND column_name='experiment'") == '0'
    start_api(); assert dump() == baseline
    assert request('people?id=eq.10000000-0000-0000-0000-000000000001','DELETE') == []
    assert len(request('rpc/semantic_search_books','POST', {'embedding_input':vector,'min_similarity':.8})) == 2
    smoke_ui(baseline)
    succeeded = True
finally:
    stop_api(); gateway.shutdown(); gateway.server_close(); log.close()
    if next_server:
        next_server.terminate(); next_server.wait(timeout=30)
    cleanup_errors = []
    for cleanup in [lambda: project('stop'), lambda: db('stop','seed')]:
        try: cleanup()
        except Exception as error: cleanup_errors.append(str(error))
    if succeeded:
        assert not cleanup_errors, cleanup_errors
        assert db('list')['active_clusters'] == 0
        assert not list((STORE/'generations').glob('*/data/postmaster.pid'))
        summary = {
            'booklist_revision': run(['git','-C',BOOKLIST,'rev-parse','HEAD']).strip(),
            'source_sha256': {name:hashlib.sha256((BOOKLIST/name).read_bytes()).hexdigest() for name in ['scripts/dump-data.ts','utils/supabase/server.ts']},
            'postgres': run([PG/'postgres','--version']).strip(),
            'postgrest': run([POSTGREST,'--version']).strip(),
            'checks': ['unchanged dump-data.ts via supabase-js HTTP RPC', 'synthetic books and recommendations', '1536-dimensional vector search', 'anon DELETE denied by RLS', 'paired source/schema/data restore', 'Next dev HTTP 200 and generated JSON served', 'servers stopped'],
            'production_data_used': False, 'hosted_database_modified': False}
        (ROOT/'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
        (ROOT/'SUCCESS').write_text('All checks and teardown passed.\n')
        print('PASS: Booklist supabase-js/PostgREST, RLS, vector search, paired rewind, Next HTTP smoke, teardown')
        print('Artifacts:', ROOT)
    elif cleanup_errors:
        print('Cleanup errors:', *cleanup_errors, file=sys.stderr)
