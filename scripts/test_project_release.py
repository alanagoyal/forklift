#!/usr/bin/env python3
"""Check a normal release against the disposable fixture from test_project.py."""
import json
import os
from pathlib import Path
import subprocess
import sys

root = Path(sys.argv[1]).resolve()
assert (root/'SUCCESS').exists(), 'run test_project.py successfully first'
repo = Path(__file__).resolve().parents[1]
binary = Path(os.environ.get('FORKLIFT_BIN', repo/'target/release/forklift'))
work = root/'python-work'
records = []

def run(*args, expected=0, env=None):
    p = subprocess.run([str(binary),'--json','project','--root',str(work),*args], capture_output=True, text=True, env=env, timeout=180)
    records.append({'args':args,'exit':p.returncode,'stdout':p.stdout,'stderr':p.stderr})
    assert p.returncode == expected, (p.stdout,p.stderr)
    return p.stdout

config = work/'.forklift.json'; original = config.read_bytes()
try:
    assert run('task','verify').strip() == 'v1 3'
    changed = json.loads(original); changed['connection_env'] = 'CHANGED_DB'
    config.write_text(json.dumps(changed))
    run('task','verify',expected=1)
    run('recover')
    run('stop')
    config.write_bytes(original)
    env = os.environ.copy()
    env['FORKLIFT_PROJECT_TEST_FAILPOINT'] = 'restore_removed'
    env['FORKLIFT_PAIR_TEST_FAILPOINT'] = 'restore_published'
    env['FORKLIFT_FAILPOINT'] = 'cache_branch_copied'
    run('restore','baseline',env=env)
    assert run('task','verify').strip() == 'v1 3'
finally:
    config.write_bytes(original)
    run('recover')
    run('stop')
    view = subprocess.run([str(binary),'--json','--home',str(root/'store'),'list'],capture_output=True,text=True,check=True)
    cleanup = json.loads(view.stdout)['result']
    assert cleanup['active_clusters'] == 0
    assert not list((root/'store').glob('generations/*/data/postmaster.pid'))
    (root/'release-cleanup.json').write_text(json.dumps(cleanup,indent=2))
    (root/'release-checks.json').write_text(json.dumps(records,indent=2))
print('configuration-drift teardown and release failpoint checks passed')
