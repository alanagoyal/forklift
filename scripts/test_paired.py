#!/usr/bin/env python3
"""Deterministic real-Postgres recovery tests, NOT an authentic agent run."""
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import signal
import time
from demo_paired import prepare, observe, sql, save, REPO
from paired import Workspace, inventory

Path('benchmark-data').mkdir(exist_ok=True)
root=Path(tempfile.mkdtemp(prefix='paired-tests-',dir='benchmark-data')).resolve()
evidence=root/'evidence'
prepare(root/'run',evidence)
w=Workspace(root/'run/workspace')
records=[]


def invoke(*args, fault=None, rust_fault=None, expected=0):
    env=os.environ.copy()
    env.pop('FORKLIFT_PAIR_TEST_FAILPOINT',None)
    env.pop('FORKLIFT_FAILPOINT',None)
    if fault: env['FORKLIFT_PAIR_TEST_FAILPOINT']=fault
    if rust_fault: env['FORKLIFT_FAILPOINT']=rust_fault
    p=subprocess.run([sys.executable,str(REPO/'scripts/paired.py'),str(w.root),*args],env=env,capture_output=True,text=True,timeout=180)
    records.append({'args':args,'fault':fault,'rust_fault':rust_fault,'exit':p.returncode,'stderr':p.stderr})
    assert p.returncode==expected,(args,p.stdout,p.stderr)


try:
    baseline=observe(w)
    # Dirty bytes and an untracked file are captured; no Git metadata required.
    code=w.root/w.current()['code']
    (code/'notes.txt').write_text('untracked work\n')
    (code/'README.txt').write_text('dirty source\n')
    invoke('capture','dirty')
    dirty=inventory(code)
    (code/'notes.txt').write_text('later edits retained in old generation\n')
    invoke('restore','dirty')
    assert inventory(w.root/w.current()['code'])==dirty
    assert (code/'notes.txt').read_text()=='later edits retained in old generation\n'
    invoke('restore','baseline')
    assert observe(w)==baseline

    for stage in ('capture_code','capture_database','capture_published'):
        name=stage
        before=w.current()
        invoke('capture',name,fault=stage,expected=99)
        invoke('run','--',sys.executable,'app.py',expected=1)
        assert w.current()==before
        invoke('recover')
        assert observe(w)==baseline
        assert (w.root/'pairs'/f'{name}.json').exists()==(stage=='capture_published')

    # Failure while the Rust engine has stopped the source: pair recovery resumes it.
    invoke('capture','rust_interrupted',rust_fault='checkpoint_stopped',expected=1)
    invoke('recover')
    assert observe(w)==baseline

    for stage in ('restore_code','restore_database','restore_started','restore_published'):
        # Deterministic mutation for interruption tests, explicitly not agent evidence.
        old=w.current()
        (w.root/old['code']/'unsaved.txt').write_text(stage)
        sql(w,old['branch'],"UPDATE invoices SET customer='changed' WHERE id=1")
        invoke('restore','baseline',fault=stage,expected=99)
        invoke('run','--',sys.executable,'app.py',expected=1)
        invoke('recover')
        if stage=='restore_published':
            assert observe(w)==baseline
            assert w.current()!=old
        else:
            assert w.current()==old
            assert sql(w,old['branch'],'SELECT customer FROM invoices WHERE id=1')=='changed'
            assert (w.root/old['code']/'unsaved.txt').read_text()==stage
        assert (w.root/old['code']/'unsaved.txt').read_text()==stage
        invoke('restore','baseline')
        assert observe(w)==baseline

    # Interrupted database preparation also keeps the old pair selected.
    old=w.current()
    invoke('restore','baseline',rust_fault='cache_branch_copied',expected=1)
    invoke('recover')
    assert w.current()==old and observe(w)==baseline

    # Running-limit refusal leaves the old pair intact, with explicit recovery.
    old=w.current()
    w.cli('config','--max-running','1')
    invoke('restore','baseline',expected=1)
    invoke('recover')
    assert w.current()==old and observe(w)==baseline
    w.cli('config','--max-running','3')

    # A synchronous child inherits the gate: killing its coordinator must not
    # let capture race the still-running child.
    marker=root/'child.pid'
    child_code="import os,time; open("+repr(str(marker))+",'w').write(str(os.getpid())); time.sleep(30)"
    parent=subprocess.Popen([sys.executable,str(REPO/'scripts/paired.py'),str(w.root),'run','--',sys.executable,'-c',child_code],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        deadline=time.monotonic()+15
        while not marker.exists() and time.monotonic()<deadline:
            time.sleep(.05)
        assert marker.exists()
        parent.kill(); parent.wait()
        invoke('capture','child_still_running',expected=1)
    finally:
        if marker.exists(): os.kill(int(marker.read_text()),signal.SIGTERM)
        if parent.poll() is None: parent.kill(); parent.wait()
    # Retry lock acquisition only until the terminated child has released it.
    deadline=time.monotonic()+5
    while True:
        with (w.root/'gate.lock').open('a') as lock:
            try:
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                break
            except BlockingIOError:
                assert time.monotonic()<deadline
                time.sleep(.05)

    with (w.root/'gate.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        invoke('capture','busy',expected=1)
    code=w.root/w.current()['code']
    (code/'bad-link').symlink_to('/etc/hosts')
    invoke('capture','unsafe',expected=1)
    invoke('recover')
    (code/'bad-link').unlink()
    pair=json.loads((w.root/'pairs/baseline.json').read_text())
    saved=w.root/pair['code']/'app.py'
    original=saved.read_bytes()
    saved.write_bytes(original+b'\n# corrupt\n')
    invoke('restore','baseline',expected=1)
    saved.write_bytes(original)
    assert observe(w)==baseline
    for name in ('source','sibling'):
        assert json.loads(sql(w,name,'SELECT json_agg(i ORDER BY id) FROM invoices i'))==baseline['data']
        w.cli('stop',name)
finally:
    # Recover if possible, then park every owned branch, including failed creations.
    w.recover()
    for b in w.cli('list')['branches']:
        if b['state']!='failed': w.cli('stop',b['name'])
    view=w.cli('list')
    save(evidence/'test-results.json',records)
    save(evidence/'cleanup.json',view)
assert view['active_clusters']==0
assert not list(w.store.glob('generations/*/data/postmaster.pid'))
(evidence/'SUCCESS').write_text('paired interruption and preservation tests passed\n')
print(evidence)
