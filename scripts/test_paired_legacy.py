#!/usr/bin/env python3
"""Verify Rust opens/recover/restores actual format-1 Python workspaces.
The retired implementation is loaded from its immutable historical Git revision,
only inside an ignored test directory. It is not shipped as a second coordinator.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from demo_paired import prepare, observe, sql, save, REPO
from paired_support import Workspace, pair_json, BIN

LEGACY_COMMIT = '36cf2322b00c5d81d372888864e15b946e7e0fb7'
(REPO/'benchmark-data').mkdir(exist_ok=True)
root = Path(tempfile.mkdtemp(prefix='paired-legacy-', dir=REPO/'benchmark-data'))
evidence = root/'evidence'
legacy = root/'legacy.py'
legacy.write_bytes(subprocess.check_output(['git','show',f'{LEGACY_COMMIT}:scripts/paired.py'],cwd=REPO))
prepare(root/'run', evidence)
store = root/'run/store'
workspace = root/'legacy-workspace'
records=[]

def old(*args, fault=None, expected=0):
    env=os.environ.copy()
    env['FORKLIFT_BIN']=str(BIN)
    if fault: env['FORKLIFT_PAIR_TEST_FAILPOINT']=fault
    else: env.pop('FORKLIFT_PAIR_TEST_FAILPOINT',None)
    p=subprocess.run([sys.executable,str(legacy),str(workspace),*map(str,args)],env=env,capture_output=True,text=True,timeout=180)
    records.append({'args':list(map(str,args)),'fault':fault,'exit':p.returncode})
    assert p.returncode==expected,(args,p.stdout,p.stderr)

w=Workspace(root/'run/workspace')
try:
    old('init','--source',REPO/'fixtures/invoice-app','--store',store,'--checkpoint','seed')
    w=Workspace(workspace)
    baseline=observe(w)
    old('capture','baseline')
    save(evidence/'legacy-baseline.json',baseline)
    (workspace/w.current()['code']/'unsaved.txt').write_text('preserve this old work')
    sql(w,w.current()['branch'],"UPDATE invoices SET customer='legacy modified' WHERE id=1")
    pair_json(workspace,'restore','baseline')
    assert observe(w)==baseline
    old('capture','interrupted',fault='capture_database',expected=99)
    pair_json(workspace,'recover')
    assert observe(w)==baseline
    for stage in ('restore_started','restore_published'):
        before=w.current()
        old('restore','baseline',fault=stage,expected=99)
        pair_json(workspace,'recover')
        assert (w.current()==before)==(stage=='restore_started')
        assert observe(w)==baseline
    save(evidence/'compatibility.json',{'legacy_commit':LEGACY_COMMIT,'operations':records,'restored':observe(w),'passed':True})
finally:
    w.recover()
    for b in w.cli('list')['branches']:
        if b['state']!='failed': w.cli('stop',b['name'])
    view=w.cli('list')
    save(evidence/'cleanup.json',view)
assert view['active_clusters']==0
assert not list(store.glob('generations/*/data/postmaster.pid'))
(evidence/'SUCCESS').write_text('historical Python format-1 capture, restore and interrupted journal compatibility passed\n')
print(evidence)
