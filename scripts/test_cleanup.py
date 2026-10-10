#!/usr/bin/env python3
"""Real PostgreSQL cleanup safety, retained data, preview and interruption checks."""
import json
import os
from pathlib import Path
import subprocess
import tempfile

PG = Path(os.environ.get('PG_BIN', '/opt/homebrew/opt/postgresql@17/bin'))
BIN = Path(os.environ.get('FORKLIFT_BIN', 'target/release/forklift')).resolve()
Path('benchmark-data').mkdir(exist_ok=True)
ROOT = Path(tempfile.mkdtemp(prefix='cleanup-', dir='benchmark-data')).resolve()
HOME = ROOT/'store'
records = []


def cli(*args, code=None, fault=None):
    env = os.environ.copy(); env.pop('FORKLIFT_FAILPOINT', None)
    if fault: env['FORKLIFT_FAILPOINT'] = fault
    p = subprocess.run([str(BIN), '--home', str(HOME), '--pg-bin', str(PG), '--json', *args],
                       env=env, text=True, capture_output=True, timeout=180)
    if fault:
        assert p.returncode == 99, (args, p.stdout, p.stderr)
        records.append({'args': args, 'fault': fault}); return
    obj = json.loads(p.stdout)
    if code: assert not obj['ok'] and obj['error']['code'] == code, (args, obj)
    else: assert p.returncode == 0 and obj['ok'], (args, obj, p.stderr)
    records.append({'args': args, 'response': obj})
    return obj.get('result')


def cat(): return json.loads((HOME/'catalog.json').read_text())
def sql(name, query):
    conn = cli('connection', name)['connection']
    return subprocess.check_output([str(PG/'psql'), conn, '-XAt', '-v', 'ON_ERROR_STOP=1', '-c', query], text=True).strip()
def head(): return HOME/'generations'/cat()['branches']['demo']['head']['id']

try:
    print('preview, actual reclaim and preserved checkpoint data', flush=True)
    cli('init', 'demo'); cli('start', 'demo')
    sql('demo', "CREATE TABLE retained(value text); INSERT INTO retained VALUES('seed')")
    cli('checkpoint', 'demo', '--name', 'seed')
    old = head(); cli('reset', 'demo', '--to', 'seed', '--yes')
    selected = head()
    catalog_before = (HOME/'catalog.json').read_bytes()
    plan = cli('cleanup')
    assert not plan['applied'] and plan['apparent_bytes'] > 0
    assert old.exists() and selected.exists()
    assert (HOME/'catalog.json').read_bytes() == catalog_before
    paths = [HOME/c['path'] for c in plan['candidates']]
    assert old in paths and selected not in paths
    before = cli('stats')['total']['apparent_bytes']
    cli('cleanup', '--yes')
    assert all(not p.exists() for p in paths)
    assert cli('stats')['total']['apparent_bytes'] < before
    assert sql('demo', 'SELECT value FROM retained') == 'seed'
    cli('branch', '--from', 'seed', '--name', 'check')
    cli('start', 'check'); assert sql('check', 'SELECT value FROM retained') == 'seed'; cli('stop', 'check')
    cli('export', 'seed', '--output', str(ROOT/'seed.forklift'))
    cache = HOME/'cache'/cat()['checkpoints']['seed']['manifest']
    (cache/'data/PG_VERSION').write_text('18\n')
    cli('branch', '--from', 'seed', '--name', 'rebuilt')
    quarantined = list((HOME/'cache').glob('.invalid-*')); assert quarantined
    cli('cleanup', '--yes'); assert all(not p.exists() for p in quarantined)

    print('ownership, links, pidfiles and full-plan safety', flush=True)
    old = head(); cli('reset', 'demo', '--to', 'seed', '--yes')
    marker = old/'owner.json'; saved = marker.read_bytes()
    marker.write_text('{}'); cli('cleanup', '--yes', code='catalog'); marker.write_bytes(saved)
    bad = json.loads(saved); bad['store_id'] = 'wrong'; marker.write_text(json.dumps(bad))
    cli('cleanup', '--yes', code='unsafe_path'); marker.write_bytes(saved)
    sentinel = ROOT/'sentinel'; sentinel.write_text('untouched')
    link = old/'data/unsafe'; link.symlink_to(sentinel)
    cli('cleanup', '--yes', code='unsafe_path'); link.unlink()
    os.link(sentinel, link); cli('cleanup', '--yes', code='unsafe_path'); link.unlink()
    pid = old/'data/postmaster.pid'; pid.write_text(str(os.getpid()))
    cli('cleanup', '--yes', code='not_stopped'); assert old.exists(); pid.unlink()
    assert sentinel.read_text() == 'untouched'
    # A genuinely running orphan must also be refused without signaling it.
    socket = Path(tempfile.mkdtemp(prefix='fl-clean-', dir='/tmp'))
    subprocess.run([str(PG/'pg_ctl'), '-D', str(old/'data'), '-l', str(old/'test.log'),
                    '-o', "-c listen_addresses='' -k " + str(socket), '-w', 'start'], check=True, capture_output=True)
    cli('cleanup', '--yes', code='not_stopped')
    subprocess.run([str(PG/'pg_ctl'), '-D', str(old/'data'), '-m', 'fast', '-w', 'stop'], check=True, capture_output=True)

    socket.rmdir()

    print('pending replacements protected; journal resumes safely', flush=True)
    cli('reset', 'demo', '--to', 'seed', '--yes', fault='replacement_ready')
    c = cat(); pending = HOME/'generations'/c['branches']['demo']['pending']['id']
    snapshot = (HOME/'catalog.json').read_bytes()
    plan = cli('cleanup')
    assert pending.exists() and all(HOME/e['path'] != pending for e in plan['candidates'])
    assert (HOME/'catalog.json').read_bytes() == snapshot
    cli('cleanup', '--yes', fault='cleanup_journaled')
    # Simulate a process dying midway through recursive removal of a candidate.
    journal = json.loads((HOME/'cleanup.json').read_text())
    for relative in journal['paths']:
        candidate = HOME/relative
        marker = candidate/('owner.json' if relative.startswith('generations/') else 'entry.json')
        if marker.exists():
            saved = marker.read_bytes(); bad = json.loads(saved); bad['store_id'] = 'wrong'
            marker.write_text(json.dumps(bad)); cli('cleanup', '--yes', code='unsafe_path')
            marker.write_bytes(saved)
    first = HOME/journal['paths'][0]
    for p in first.rglob('*'):
        if p.is_file(): p.unlink(); break
    assert cli('cleanup')['resuming']
    cli('cleanup', '--yes', fault='cleanup_removed')
    cli('cleanup', '--yes')
    assert pending.exists() and not (HOME/'cleanup.json').exists()
    cli('list')  # normal reconciliation abandons pending generation
    cli('cleanup', '--yes'); assert not pending.exists()
    assert sql('demo', 'SELECT value FROM retained') == 'seed'

    print('incomplete cache retained names and conservative legacy protection', flush=True)
    cli('cleanup', '--yes')
    cli('branch', '--from', 'seed', '--name', 'failed-cache', fault='cache_before_publish')
    c = cat(); failed = HOME/'generations'/c['branches']['failed-cache']['head']['id']
    builds = list((HOME/'cache').glob('.building-*')); assert builds
    cli('cleanup', '--yes'); assert failed.exists() and all(not p.exists() for p in builds)
    # Legacy checkpoints use a generation rather than a manifest; retain them.
    cli('list'); cli('stop', 'demo')
    old = head(); cli('reset', 'demo', '--to', 'seed', '--yes')
    c = cat(); c['checkpoints']['legacy'] = {'instance': {'id': old.name, 'state': 'ready', 'process': None},
        'source': 'demo', 'copy_mode': 'apfs', 'manifest': None}
    (HOME/'catalog.json').write_text(json.dumps(c))
    cli('cleanup', '--yes'); assert old.exists()
    cli('branch', '--from', 'legacy', '--name', 'legacy-check')
    cli('start', 'legacy-check'); assert sql('legacy-check', 'SELECT value FROM retained') == 'seed'
    cli('stop', 'legacy-check')
    cli('cleanup', '--yes'); assert cli('cleanup')['candidates'] == []
    (ROOT/'SUCCESS').write_text('cleanup safety, reclamation and interruption checks passed\n')
    print('PASS: '+str(ROOT), flush=True)
finally:
    for data in (HOME/'generations').glob('*/data'):
        pid = data/'postmaster.pid'
        if pid.exists() and len(pid.read_text().splitlines()) > 1 and pid.read_text().splitlines()[1] == str(data):
            subprocess.run([str(PG/'pg_ctl'), '-D', str(data), '-m', 'fast', '-w', 'stop'], capture_output=True)
    (ROOT/'test-results.json').write_text(json.dumps(records, indent=2)+'\n')
