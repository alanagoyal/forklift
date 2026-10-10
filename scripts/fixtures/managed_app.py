#!/usr/bin/env python3
"""Disposable real HTTP app + database/source-writing worker; lifecycle hook fixture.
Ownership uses a private lock plus authenticated loopback control, never PID kills.
Only the daemon closes the inherited Forklift gate; synchronous hooks retain it.
"""
import fcntl
import http.server
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time
import urllib.request

STATE = Path(os.environ['FORKLIFT_MANAGED_STATE'])/'managed-fixture'
STATE.mkdir(mode=0o700, exist_ok=True)
ROOT = Path.cwd()

def locked():
    with (STATE/'app.lock').open('a') as f:
        try: fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: return True
    return False

def request(path):
    info = json.loads((STATE/'address.json').read_text())
    with urllib.request.urlopen(f"http://127.0.0.1:{info['port']}/{info['token']}/{path}", timeout=10) as r:
        return r.read()

def sql(query):
    return subprocess.check_output([os.environ['PG_BIN']+'/psql', os.environ['DATABASE_URL'],
        '-XqAt', '-v', 'ON_ERROR_STOP=1', '-c', query], text=True).strip()

def event(kind, **data):
    with (ROOT/'.fixture-events').open('a') as f:
        f.write(json.dumps(dict(kind=kind, **data))+'\n')

mode = sys.argv[1]
if mode == 'daemon':
    lock = (STATE/'app.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    token = secrets.token_hex(16)
    stopping = threading.Event()
    worker_error = []
    def worker():
        try:
            while not stopping.is_set():
                value = sql('UPDATE managed_fixture SET value=value+1 RETURNING value')
                # Deliberately span a DB commit and source write; stop must drain both.
                time.sleep(0.06)
                (ROOT/'src/value').write_text(value)
                stopping.wait(0.07)
        except BaseException as e:
            worker_error.append(str(e))
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if not self.path.startswith('/'+token+'/'):
                self.send_error(403); return
            verb = self.path.rsplit('/', 1)[1]
            self.send_response(200); self.end_headers()
            if verb == 'stop':
                stopping.set()
                threading.Thread(target=server.shutdown).start()
            elif verb == 'slow':
                (ROOT/'.fixture-request').write_text('active')
                time.sleep(0.6)
                (ROOT/'.fixture-request').unlink()
            self.wfile.write(b'ok')
        def log_message(self, *args): pass
    server = http.server.HTTPServer(('127.0.0.1', 0), Handler)
    event('start', database=sql('SELECT value FROM managed_fixture'), source=(ROOT/'src/value').read_text())
    (STATE/'address.json').write_text(json.dumps({'port':server.server_port, 'token':token}))
    thread = threading.Thread(target=worker); thread.start()
    try: server.serve_forever(poll_interval=0.02)
    finally:
        stopping.set(); thread.join(); server.server_close()
        event('stop', database=sql('SELECT value FROM managed_fixture'), source=(ROOT/'src/value').read_text(), errors=worker_error)
    sys.exit(0)

if (ROOT/f'.fixture-{mode}-entered').exists():
    (ROOT/f'.fixture-{mode}-entered').write_text('entered')
while (ROOT/f'.fixture-{mode}-hold').exists():
    time.sleep(0.025)
if (ROOT/f'.fixture-{mode}-delay').exists():
    time.sleep(float((ROOT/f'.fixture-{mode}-delay').read_text()))
if (ROOT/f'.fixture-{mode}-fail').exists(): sys.exit(7)

if mode == 'status':
    running = locked()
    if running and (ROOT/'.fixture-status-partial').exists(): sys.exit(1)
    sys.exit(0 if running else 3)
if mode == 'start':
    assert not locked()
    with (STATE/'app.log').open('a') as log:
        subprocess.Popen([sys.executable, __file__, 'daemon'], stdin=subprocess.DEVNULL,
            stdout=log, stderr=log, close_fds=True, start_new_session=True)
    for _ in range(200):
        if locked():
            try:
                request('health')
                if (ROOT/'.fixture-start-partial-fail').exists(): sys.exit(8)
                sys.exit(0)
            except (OSError, ValueError): pass
        time.sleep(0.025)
    sys.exit(9)
if mode == 'stop':
    if (ROOT/'.fixture-stop-noop').exists(): sys.exit(0)
    if locked():
        request('stop')
        for _ in range(400):
            if not locked(): sys.exit(0)
            time.sleep(0.025)
        sys.exit(9)
    sys.exit(0)
raise ValueError(mode)
