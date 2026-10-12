#!/usr/bin/env python3
"""Opt-in real native Supabase backend integration (no hosted services/data).
FORKLIFT_BIN must be built with test-failpoints. SUPABASE_BIN pins CLI 2.120.0.
SUPABASE_TEST_CACHE optionally reuses downloaded native artifacts by APFS clone.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

REPO = Path(__file__).resolve().parents[1]
BIN = Path(os.environ['FORKLIFT_BIN']).resolve()
CLI = Path(os.environ['SUPABASE_BIN']).resolve()
PG = Path(os.environ.get('PG_BIN', '/opt/homebrew/opt/postgresql@17/bin')).resolve()
CACHE = Path(os.environ['SUPABASE_TEST_CACHE']).resolve() if os.environ.get('SUPABASE_TEST_CACHE') else None
(REPO/'benchmark-data').mkdir(exist_ok=True)
ROOT = Path(tempfile.mkdtemp(prefix='supabase-', dir=REPO/'benchmark-data'))
ENV = {k:v for k,v in os.environ.items() if k in ['PATH','HOME','TMPDIR','LANG']}
ENV['FORKLIFT_SUPABASE_BIN'] = str(CLI)
roots = []
states = []
checks = []

def command(args, expected=0, env=None, cwd=None):
    p = subprocess.run(list(map(str,args)), capture_output=True, text=True, env=env or ENV, cwd=cwd, timeout=600)
    if p.returncode != expected:
        # CLI connection output contains credentials: never include stdout or argv.
        detail=''
        try: detail=json.loads(p.stdout).get('error',{}).get('message','')
        except (ValueError,AttributeError): pass
        raise AssertionError(f'command exit {p.returncode}, expected {expected}; {detail}; stderr: {p.stderr[-1500:]}')
    return p.stdout.strip()

def project(root,*args,expected=0,extra_env=None):
    return command([BIN,'--json','--pg-bin',PG,'project','--root',root,*args],expected=expected,env=dict(ENV,**(extra_env or {})))

def result(root,*args): return json.loads(project(root,*args))['result']
def git(root,*args): return command(['git','-C',root,*args])
def state(root):
    admin=Path(git(root,'rev-parse','--absolute-git-dir'))
    common=Path(git(root,'rev-parse','--git-common-dir'))
    if not common.is_absolute(): common=(root/common).resolve()
    return common/'forklift-workspaces'/json.loads((admin/'forklift-id.json').read_text())

def setup(root):
    roots.append(root)
    # A missing CLI must fail retryably, before storing its path. This also allocates
    # the private workspace, allowing optional reuse of already downloaded artifacts.
    project(root,'setup',expected=1,extra_env={'FORKLIFT_SUPABASE_BIN':str(ROOT/'missing-cli')})
    st=state(root);states.append(st)
    if CACHE:
        (st/'home').mkdir(mode=0o700,exist_ok=True)
        command(['/bin/cp','-cR',CACHE,st/'home/cache'])
    project(root,'setup')
    print('native setup ready:',root.name,flush=True)
    assert result(root,'doctor')['ready']
    assert result(root,'inspect')['backend']=='supabase'
    assert not (root/'supabase/.temp').exists()
    assert json.loads((root/'.forklift.json').read_text())['backend']=='supabase'
    return st

def environment(root):
    code='import os,json; print(json.dumps({k:os.environ[k] for k in ["DATABASE_URL","NEXT_PUBLIC_SUPABASE_URL","NEXT_PUBLIC_SUPABASE_ANON_KEY","SUPABASE_SERVICE_ROLE_KEY"]}))'
    e=json.loads(project(root,'run','--',sys.executable,'-c',code,extra_env={'PGHOSTADDR':'192.0.2.1','SUPABASE_SERVICE_ROLE_KEY':'hosted-must-be-replaced','NEXT_PUBLIC_SUPABASE_URL':'https://invalid.example'}))
    assert urllib.parse.urlsplit(e['DATABASE_URL']).hostname in ['127.0.0.1','localhost']
    assert urllib.parse.urlsplit(e['NEXT_PUBLIC_SUPABASE_URL']).hostname in ['127.0.0.1','localhost']
    assert e['SUPABASE_SERVICE_ROLE_KEY']!='hosted-must-be-replaced'
    return e

def sql(e,statement):
    return command([PG/'psql',e['DATABASE_URL'],'-XAt','-v','ON_ERROR_STOP=1','-c',statement])

def api(e,path,body=None,method='POST',raw=False,anon=False,expected=200):
    key=e['NEXT_PUBLIC_SUPABASE_ANON_KEY'] if anon else e['SUPABASE_SERVICE_ROLE_KEY']
    request=urllib.request.Request(e['NEXT_PUBLIC_SUPABASE_URL']+path,method=method,
        data=body if raw else json.dumps(body).encode() if body is not None else None,
        headers={'apikey':key,'Authorization':'Bearer '+key,'Content-Type':'text/plain' if raw else 'application/json','x-upsert':'true'})
    try:
        with urllib.request.urlopen(request,timeout=30) as response:
            status=response.status;payload=response.read()
    except urllib.error.HTTPError as error:
        status=error.code;payload=error.read()
    assert status==expected,(path,status,expected)
    return payload if raw else json.loads(payload) if payload else None

def signin(e,expected=200):
    return api(e,'/auth/v1/token?grant_type=password',{'email':'forklift-fixture@example.test','password':'local-synthetic-password-2026'},anon=True,expected=expected)

def verify_baseline(root,e,uid):
    assert (root/'src/app.txt').read_text()=='baseline\n'
    assert sql(e,'SELECT value FROM public.fixture')=='baseline'
    assert sql(e,"SELECT count(*) FROM information_schema.columns WHERE table_schema='public' AND table_name='fixture' AND column_name='experiment'")=='0'
    assert signin(e)['user']['id']==uid
    assert api(e,'/storage/v1/object/fixture/note.txt',method='GET',raw=True)==b'baseline object'
    assert sql(e,"SELECT current_setting('fsync')||','||current_setting('full_page_writes')||','||current_setting('synchronous_commit')")=='on,on,on'

success=False
try:
    root=ROOT/'app';root.mkdir();git(root,'init')
    (root/'src').mkdir();(root/'src/app.txt').write_text('baseline\n')
    (root/'supabase').mkdir()
    (root/'supabase/config.toml').write_text('project_id = "forklift-backend-fixture"\n[db]\nmajor_version = 17\n[auth.email]\nenable_confirmations = false\n')
    git(root,'add','.');git(root,'-c','user.name=fixture','-c','user.email=fixture@example.test','commit','-m','native fixture')
    st=setup(root)
    git(root,'add','.forklift.json');git(root,'-c','user.name=fixture','-c','user.email=fixture@example.test','commit','-m','local backend configuration')
    e=environment(root)
    sql(e,"CREATE TABLE public.fixture(value text); INSERT INTO public.fixture VALUES('baseline')")
    uid=api(e,'/auth/v1/admin/users',{'email':'forklift-fixture@example.test','password':'local-synthetic-password-2026','email_confirm':True})['id']
    api(e,'/storage/v1/bucket',{'id':'fixture','name':'fixture','public':False})
    api(e,'/storage/v1/object/fixture/note.txt',b'baseline object',raw=True)
    project(root,'capture','baseline')
    print('baseline capture ready',flush=True)
    (root/'src/app.txt').write_text('changed\n')
    sql(e,"ALTER TABLE public.fixture ADD COLUMN experiment boolean DEFAULT true; UPDATE public.fixture SET value='changed'")
    api(e,'/auth/v1/admin/users/'+uid,method='DELETE')
    signin(e,expected=400)
    api(e,'/storage/v1/object/fixture/note.txt',b'changed object',raw=True)
    project(root,'restore','baseline');e=environment(root);verify_baseline(root,e,uid)
    checks.append('source/schema/data/Auth user-password/private Storage restore')
    # Immutable snapshot corruption must be rejected before current state changes.
    captured_source=st/'captures/baseline/data/source/src/app.txt'
    original=captured_source.read_bytes()
    try:
        captured_source.write_bytes(b'corrupt capture')
        project(root,'restore','baseline',expected=1)
        verify_baseline(root,e,uid)
    finally: captured_source.write_bytes(original)
    # A managed child retains the cooperative gate until it exits.
    ready=ROOT/'child-ready';release=ROOT/'child-release'
    child_code=('import pathlib,time; pathlib.Path('+repr(str(ready))+').write_text("ready"); '
                'exec("while not pathlib.Path('+repr(str(release))+').exists(): time.sleep(0.05)")')
    child=subprocess.Popen([str(BIN),'--json','--pg-bin',str(PG),'project','--root',str(root),'run','--',sys.executable,'-c',child_code],env=ENV,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    try:
        for _ in range(1200):
            if ready.exists(): break
            assert child.poll() is None,'managed child exited before ready'
            time.sleep(.05)
        assert ready.exists(),'managed child did not start'
        project(root,'capture','locked',expected=1)
    finally:
        release.write_text('release')
        child.communicate(timeout=60)
        assert child.returncode==0
    # Refuse a symlink into caller-owned storage before starting or copying state.
    project(root,'stop')
    temp=st/'runtime/supabase/.temp';parked=temp.with_name('.temp-parked')
    outside=ROOT/'outside';outside.mkdir();sentinel=outside/'sentinel';sentinel.write_text('untouched')
    temp.rename(parked);temp.symlink_to(outside,target_is_directory=True)
    try:
        project(root,'setup',expected=1)
        assert sentinel.read_text()=='untouched' and list(outside.iterdir())==[sentinel]
    finally:
        temp.unlink();parked.rename(temp)
    project(root,'setup');e=environment(root);verify_baseline(root,e,uid)
    checks.append('corrupt snapshot refusal, managed child lock, external symlink refusal')
    # Each crash boundary must remain recoverable and block ordinary managed work.
    for stage in ['restore_journal','restore_removed','restore_database','restore_item','restore_files','restore_started']:
        (root/'src/app.txt').write_text('fault '+stage+'\n')
        project(root,'restore','baseline',expected=99,extra_env={'FORKLIFT_SUPABASE_TEST_FAILPOINT':stage})
        assert (st/'pending.json').exists()
        project(root,'run','--',sys.executable,'-c','pass',expected=1)
        project(root,'recover');assert not (st/'pending.json').exists()
        e=environment(root);verify_baseline(root,e,uid)
        print('recovery passed:',stage,flush=True)
    checks.append('six interrupted-restore boundaries and recovery')
    # Capture publication crash leaves a reclaimable staging tree, not a named capture.
    project(root,'capture','unpublished',expected=99,extra_env={'FORKLIFT_SUPABASE_TEST_FAILPOINT':'capture_ready'})
    assert 'unpublished' not in result(root,'inspect')['captures']
    project(root,'setup')
    project(root,'capture','temporary')
    assert not result(root,'forget','temporary')['applied']
    assert 'temporary' in result(root,'inspect')['captures']
    project(root,'forget','temporary','--yes')
    preview=result(root,'prune');assert preview['paths'] and not preview['applied']
    assert all(Path(p).exists() for p in preview['paths'])
    project(root,'prune','--yes');assert all(not Path(p).exists() for p in preview['paths'])
    assert result(root,'inspect')['captures']==['baseline']
    project(root,'restore','baseline');e=environment(root);verify_baseline(root,e,uid)
    checks.append('capture publication failure, preview/forget/prune, retained baseline restore')
    # Two actual Git worktrees of one repository must use separate complete stacks.
    other=ROOT/'other';git(root,'worktree','add','-b','other',other)
    other_state=setup(other);other_env=environment(other)
    original_binding=(other_state/'supabase.json').read_bytes()
    try:
        (other_state/'supabase.json').write_bytes((st/'supabase.json').read_bytes())
        project(other,'inspect',expected=1)
    finally: (other_state/'supabase.json').write_bytes(original_binding)
    assert st!=other_state
    assert e['DATABASE_URL']!=other_env['DATABASE_URL']
    assert e['NEXT_PUBLIC_SUPABASE_URL']!=other_env['NEXT_PUBLIC_SUPABASE_URL']
    assert api(other_env,'/auth/v1/admin/users',method='GET')['users']==[]
    assert api(other_env,'/storage/v1/bucket',method='GET')==[]
    assert sql(other_env,"SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_name='fixture'")=='0'
    sql(other_env,"CREATE TABLE public.fixture(value text); INSERT INTO public.fixture VALUES('independent')")
    project(root,'restore','baseline');assert sql(other_env,'SELECT value FROM public.fixture')=='independent'
    checks.append('two Git worktrees use independent database/API/Auth/Storage state')
    success=True
finally:
    errors=[]
    for root in roots:
        try:
            project(root,'stop')
            project(root,'stop') # stopping an already-stopped native owner is idempotent
        except Exception as error: errors.append(str(error))
    if success:
        assert not errors,errors
        for st in states:
            assert not list((st/'home/stacks').rglob('postmaster.pid'))
            assert not list((st/'home/stacks').glob('*/owner.json'))
        (ROOT/'summary.json').write_text(json.dumps({'checks':checks,'all_stacks_stopped':True,'hosted_data_used':False},indent=2)+'\n')
        (ROOT/'SUCCESS').write_text('native Supabase backend integration passed\n')
        print('PASS: native Supabase backend, Auth/Storage rewind, fault recovery, retention, worktree isolation')
        print('Artifacts:',ROOT)
    elif errors:
        print('Cleanup errors:',*errors,file=sys.stderr)
