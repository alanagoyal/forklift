#!/usr/bin/env python3
"""Real HTTP/worker lifecycle acceptance. Optional native Supabase via --supabase.
Uses only disposable local projects. Run with a test-failpoints forklift binary.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

REPO = Path(__file__).resolve().parents[1]
BIN = Path(os.environ.get('FORKLIFT_BIN', REPO/'target/debug/forklift')).resolve()
PG = Path(os.environ.get('PG_BIN', '/opt/homebrew/opt/postgresql@17/bin'))
NATIVE = '--supabase' in sys.argv
(REPO/'benchmark-data').mkdir(exist_ok=True)
ROOT = Path(tempfile.mkdtemp(prefix='managed-native-' if NATIVE else 'managed-', dir=REPO/'benchmark-data'))
STORE = ROOT/'store'
ENV = os.environ.copy()
if NATIVE: ENV['FORKLIFT_SUPABASE_BIN'] = os.environ['SUPABASE_BIN']
# Never consult external app fixtures or hosted configuration.
ENV.pop('BOOKLIST_ROOT', None)
checks = []
roots = []
HOOK_TIMEOUT = 30

def invoke(argv, expected=0, env=None):
    p = subprocess.run(list(map(str, argv)), capture_output=True, text=True, env=env or ENV, timeout=600)
    assert p.returncode == expected, (argv, p.returncode, p.stdout[-2000:], p.stderr[-1000:])
    return p.stdout

def args(root, *a): return [BIN, '--json', '--pg-bin', PG, 'project', '--root', root, *a]
def project(root, *a, expected=0, extra=None): return invoke(args(root,*a), expected, dict(ENV, **(extra or {})))
def result(root,*a): return json.loads(project(root,*a))['result']
def db(*a): return json.loads(invoke([BIN,'--json','--pg-bin',PG,'--home',STORE,*a]))['result']
def git(root,*a): return invoke(['git','-C',root,*a]).strip()
def sql(root,q):
    return project(root,'run','--',sys.executable,'-c',
        'import os,subprocess,sys; subprocess.run([os.environ["PG_BIN"]+"/psql",os.environ["DATABASE_URL"],"-XAt","-v","ON_ERROR_STOP=1","-c",sys.argv[1]],check=True)',q).strip()
def state(root): return Path(result(root,'inspect')['state'])
def events(root): return [json.loads(line) for line in (root/'.fixture-events').read_text().splitlines()]
def flag(root,name,value='1'): (root/('.fixture-'+name)).write_text(value)
def unflag(root,name): (root/('.fixture-'+name)).unlink(missing_ok=True)
def wait_for(predicate):
    end=time.monotonic()+20
    while not predicate():
        assert time.monotonic()<end, 'fixture did not reach expected state'
        time.sleep(.025)
def phase(root): return result(root,'inspect')['managed']['phase']
def error(root,*a,code=None):
    r=json.loads(project(root,*a,expected=1))
    if code: assert r['error']['code']==code,r
    return r

def recover_after_hook(root):
    # Retry only lock contention while the released hook finishes draining.
    deadline=time.monotonic()+45
    while True:
        p=subprocess.run(list(map(str,args(root,'recover'))),env=ENV,capture_output=True,text=True,timeout=600)
        if p.returncode==0: return
        r=json.loads(p.stdout)
        assert r['error']['code'] in ['project_locked','pair_locked'],r
        assert time.monotonic()<deadline,'released hook did not finish'
        time.sleep(.05)

def request(root, verb):
    info=json.loads((state(root)/'managed-fixture/address.json').read_text())
    with urllib.request.urlopen(f"http://127.0.0.1:{info['port']}/{info['token']}/{verb}",timeout=10) as r: return r.read()

try:
    repo=ROOT/'repo';repo.mkdir();(repo/'src').mkdir();(repo/'src/value').write_text('0')
    config={'format':1,'backend':'supabase' if NATIVE else 'postgres','source':['src'],
        'managed':{k:[sys.executable,str(REPO/'scripts/fixtures/managed_app.py'),k] for k in ['start','stop','status']}}
    # CI may need more than two seconds to launch Python and the HTTP worker.
    # The timeout case below uses an explicit barrier instead of a short sleep.
    config['managed']['timeout_seconds']=HOOK_TIMEOUT
    if NATIVE:
        (repo/'supabase').mkdir(); (repo/'supabase/config.toml').write_text('project_id = "managed-fixture"\n[db]\nmajor_version = 17\n')
        config['source'].append('supabase/config.toml')
    (repo/'.forklift.json').write_text(json.dumps(config))
    git(repo,'init');git(repo,'add','.');git(repo,'-c','user.name=fixture','-c','user.email=fixture@example.test','commit','-m','fixture')
    if not NATIVE:
        db('init','seed');db('checkpoint','seed','--name','development')
        project(repo,'configure','--store',STORE,'--checkpoint','development')
    for name in ['one','two']:
        root=ROOT/name;git(repo,'worktree','add','-b',name,root);roots.append(root)
        if NATIVE and os.environ.get('SUPABASE_TEST_CACHE'):
            project(root,'setup',expected=1,extra={'FORKLIFT_SUPABASE_BIN':str(ROOT/'absent-cli')})
            admin=Path(git(root,'rev-parse','--absolute-git-dir'))
            common=Path(git(root,'rev-parse','--path-format=absolute','--git-common-dir'))
            st=common/'forklift-workspaces'/json.loads((admin/'forklift-id.json').read_text())
            (st/'home').mkdir(mode=0o700)
            invoke(['/bin/cp','-cR',os.environ['SUPABASE_TEST_CACHE'],st/'home/cache'])
        project(root,'setup')
        sql(root,'CREATE TABLE managed_fixture(value int); INSERT INTO managed_fixture VALUES(0)')
    one,two=roots
    # Recovery must drain an owned partial start even if initial status is unknown.
    flag(one,'start-partial-fail');flag(one,'status-partial');flag(one,'start-delay','2.5')
    error(one,'app-start',code='managed_start')
    unflag(one,'start-delay')
    original=(one/'.forklift.json').read_text()
    changed=json.loads(original);changed['commands']={'changed':['false']}
    (one/'.forklift.json').write_text(json.dumps(changed))
    assert result(one,'recover')['managed_app']=='stopped'
    (one/'.forklift.json').write_text(original)
    assert phase(one)=='stopped'
    unflag(one,'start-partial-fail');unflag(one,'status-partial')
    checks.append('recovery stops an owned unhealthy partial startup using pinned hooks despite edited config')
    (one/'.fixture-events').unlink()
    sql(one,'UPDATE managed_fixture SET value=0');(one/'src/value').write_text('0')
    if '--recovery-only' in sys.argv:
        (ROOT/'SUCCESS').write_text(json.dumps({'backend':'supabase' if NATIVE else 'postgres','checks':checks},indent=2)+'\n')
        print(ROOT)
        sys.exit(0)
    project(one,'capture','stopped')
    assert phase(one)=='stopped' and not (one/'.fixture-events').exists()
    project(two,'app-start');project(one,'app-start')
    error(one,'run','--','true',code='managed_running')
    assert request(one,'health')==b'ok'
    # An in-flight web request drains together with the worker before source/DB save.
    t=threading.Thread(target=request,args=(one,'slow'));t.start()
    wait_for(lambda:(one/'.fixture-request').exists())
    project(one,'capture','baseline');t.join()
    print('captured running application', flush=True)
    assert phase(one)=='running'
    e=events(one);assert e[-2]['kind']=='stop' and e[-1]['kind']=='start'
    assert e[-2]['database']==e[-2]['source']==e[-1]['database']==e[-1]['source']
    baseline=e[-2]['source'];assert int(baseline)>0
    wait_for(lambda:int((one/'src/value').read_text() or '0')>int(baseline))
    project(one,'restore','baseline')
    assert events(one)[-1]['source']==events(one)[-1]['database']==baseline
    assert phase(two)=='running' and len(events(two))==1
    checks.append('running HTTP/worker capture and restore; exact quiescent source/DB match; sibling unaffected')
    project(one,'app-stop');before=len(events(one))
    project(one,'capture','parked');project(one,'restore','stopped')
    assert phase(one)=='stopped' and len(events(one))==before
    assert sql(one,'SELECT value FROM managed_fixture')=='0'
    assert sql(one,"SELECT current_setting('fsync'),current_setting('full_page_writes'),current_setting('synchronous_commit'),current_setting('data_checksums')")=='on|on|on|on'
    checks.append('stopped app stays stopped; PostgreSQL durability and checksums enabled')
    # Failed stop cannot capture; recovery stops and deliberately does not restart.
    project(one,'app-start');flag(one,'stop-fail')
    error(one,'capture','must_not_exist',code='managed_stop')
    error(one,'app-start',code='managed_pending');error(one,'run','--','true',code='managed_pending')
    error(one,'restore','stopped',code='managed_pending')
    unflag(one,'stop-fail');project(one,'recover');assert phase(one)=='stopped'
    error(one,'restore','must_not_exist');project(one,'recover')
    # Initial partial start and resume failure both leave durable pending intent.
    flag(one,'start-partial-fail');error(one,'app-start',code='managed_start')
    unflag(one,'start-partial-fail');project(one,'recover');assert phase(one)=='stopped'
    flag(one,'start-fail');error(one,'app-start',code='managed_start')
    unflag(one,'start-fail');project(one,'recover')
    project(one,'app-start');flag(one,'start-fail');error(one,'capture','saved_before_start_failure',code='managed_start')
    unflag(one,'start-fail');project(one,'recover');project(one,'restore','saved_before_start_failure')
    project(one,'app-start');flag(one,'stop-noop')
    error(one,'capture','not_quiescent',code='managed_stop')
    unflag(one,'stop-noop');project(one,'recover')
    checks.append('failed/partial start, failed resume, failed stop, retained successful capture, explicit recovery')
    # Slow hook keeps gate even after timeout; no child is killed.
    project(one,'app-start');flag(one,'stop-hold');flag(one,'stop-entered')
    proc=subprocess.Popen(list(map(str,args(one,'capture','timeout'))),stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=ENV)
    wait_for(lambda:(one/'.fixture-stop-entered').read_text()=='entered')
    error(one,'capture','concurrent')
    assert request(two,'health')==b'ok'
    out,_=proc.communicate(timeout=HOOK_TIMEOUT+20);assert proc.returncode==1 and json.loads(out)['error']['code']=='managed_timeout'
    error(one,'recover')  # original hook still holds the gate
    unflag(one,'stop-hold');unflag(one,'stop-entered')
    recover_after_hook(one);assert phase(one)=='stopped'
    checks.append('concurrent commands excluded; timed-out hook retains gate; sibling remains live')
    # Killing the parent during a hook still leaves its child gate and journal.
    project(one,'app-start');flag(one,'stop-hold');flag(one,'stop-entered')
    proc=subprocess.Popen(list(map(str,args(one,'capture','interrupted_hook'))),stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=ENV)
    wait_for(lambda:(one/'.fixture-stop-entered').read_text()=='entered')
    proc.kill();proc.communicate(timeout=10)
    error(one,'recover')
    unflag(one,'stop-hold');unflag(one,'stop-entered');recover_after_hook(one)
    checks.append('parent interruption retains exclusion through child hook completion')
    # Durable app intent spans both sides of backend work and restart.
    for stage in ['paused','started']:
        project(one,'app-start')
        project(one,'capture','app_'+stage,expected=99,extra={'FORKLIFT_MANAGED_TEST_FAILPOINT':stage})
        error(one,'app-start',code='managed_pending')
        project(one,'recover');assert phase(one)=='stopped'
    checks.append('interruption after quiescence and after restart remains recoverable')
    # Restore interruptions across file publication recover before any app restart.
    stages=['restore_journal','restore_removed','restore_item','restore_files']
    if not NATIVE: stages.insert(1,'restore_pair')
    else: stages.insert(1,'restore_database');stages.append('restore_started')
    fault_key='FORKLIFT_SUPABASE_TEST_FAILPOINT' if NATIVE else 'FORKLIFT_PROJECT_TEST_FAILPOINT'
    for stage in stages:
        print('restore interruption:',stage,flush=True)
        project(one,'app-start');project(one,'restore','baseline',expected=99,extra={fault_key:stage})
        error(one,'app-start',code='managed_pending');error(one,'run','--','true',code='managed_pending')
        before=len(events(one));project(one,'recover');project(one,'recover')
        assert phase(one)=='stopped' and len(events(one))==before
        assert sql(one,'SELECT value FROM managed_fixture')==(one/'src/value').read_text()
    checks.append('all backend restore failpoints recover with app stopped and matching source/database')
    # Unknown status and external ownership never authorize stopping arbitrary work.
    flag(one,'status-fail');error(one,'capture','bad_status',code='managed_status')
    unflag(one,'status-fail');project(one,'recover')
    # Spoof only the read-only status response; stop would fail if it were invoked.
    record=state(one)/'managed.json';saved=record.read_text()
    project(one,'app-start');record.write_text(saved)
    before=len(events(one));error(one,'capture','unowned',code='managed_unowned')
    error(one,'recover',code='managed_unowned');assert len(events(one))==before
    # Fixture repair restores the ownership record deliberately hidden by this test.
    record.write_text(json.dumps({'format':1,'phase':'pending','owned':True}));project(one,'recover')
    checks.append('status failure blocks; unowned running group is never adopted or stopped')
    project(two,'stop');assert phase(two)=='stopped'
    (ROOT/'SUCCESS').write_text(json.dumps({'backend':'supabase' if NATIVE else 'postgres','checks':checks},indent=2)+'\n')
    print(ROOT)
finally:
    cleanup_errors=[]
    for root in roots:
        for p in root.glob('.fixture-*-fail'): p.unlink(missing_ok=True)
        for p in root.glob('.fixture-*-delay'): p.unlink(missing_ok=True)
        for p in root.glob('.fixture-*-hold'): p.unlink(missing_ok=True)
        try: recover_after_hook(root)
        except (AssertionError, subprocess.TimeoutExpired): cleanup_errors.append(f'{root.name}: hook drain failed')
        for action in ['recover','stop']:
            stopped=subprocess.run(list(map(str,args(root,action))),env=ENV,capture_output=True,timeout=600)
            if stopped.returncode: cleanup_errors.append(f'{root.name}: {action} failed')
    if cleanup_errors: (ROOT/'SUCCESS').unlink(missing_ok=True)
    assert not cleanup_errors,cleanup_errors
