#!/usr/bin/env python3
"""Private Git backend: real PostgreSQL, mixed history, corruption and interruption."""
import json, os, subprocess, tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BIN = Path(os.environ.get('FORKLIFT_BIN', REPO/'target/release/forklift')).resolve()
PG = Path(os.environ.get('PG_BIN', '/opt/homebrew/opt/postgresql@17/bin'))
(REPO/'benchmark-data').mkdir(exist_ok=True)
ROOT = Path(tempfile.mkdtemp(prefix='git-backend-', dir=REPO/'benchmark-data'))
STORE = ROOT/'store'
records = []

def cli(*args, code=0, fault=None, env=None):
    e = os.environ.copy() | (env or {})
    if fault: e['FORKLIFT_FAILPOINT'] = fault
    p = subprocess.run([str(BIN),'--json','--home',str(STORE),'--pg-bin',str(PG),*map(str,args)], env=e, capture_output=True, text=True, timeout=240)
    records.append({'args':args,'exit':p.returncode,'stdout':p.stdout,'stderr':p.stderr,'fault':fault})
    assert p.returncode == code, records[-1]
    return json.loads(p.stdout) if p.stdout.strip() else None

def sql(branch, query):
    uri = cli('connection',branch)['result']['uri']
    return subprocess.check_output([str(PG/'psql'),uri,'-XAt','-v','ON_ERROR_STOP=1','-c',query],text=True).strip()

def state(group,name):
    return next(x['state'] for x in cli('list')['result'][group] if x['name']==name)

try:
    cli('init','main'); cli('start','main')
    sql('main',"CREATE TABLE items(id int primary key, value text); INSERT INTO items VALUES(1,'original'),(2,'other')")
    cli('checkpoint','main','--name','chunked')
    # A format-five catalog written before backend fields existed still defaults to chunked.
    old=json.loads((STORE/'catalog.json').read_text()); old.pop('storage_backend')
    for checkpoint in old['checkpoints'].values(): checkpoint.pop('backend')
    (STORE/'catalog.json').write_text(json.dumps(old))
    assert cli('list')['result']['storage_backend']=='chunked'
    cli('config','--storage-backend','git')
    # Hostile inherited Git config, index, object directory and global hooks cannot run.
    marker = ROOT/'hook-ran'
    hooks = ROOT/'hooks'; hooks.mkdir()
    hook = hooks/'reference-transaction'; hook.write_text('#!/bin/sh\ntouch '+str(marker)+'\n'); hook.chmod(0o700)
    config = ROOT/'global-config'; config.write_text('[core]\n hooksPath = '+str(hooks)+'\n[filter "evil"]\n clean = '+str(hook)+'\n')
    cli('checkpoint','main','--name','git',env={'GIT_CONFIG_GLOBAL':str(config),'GIT_DIR':'/invalid','GIT_OBJECT_DIRECTORY':'/invalid','GIT_INDEX_FILE':'/invalid','GIT_CONFIG_COUNT':'1','GIT_CONFIG_KEY_0':'core.hooksPath','GIT_CONFIG_VALUE_0':str(hooks)})
    assert not marker.exists()
    catalog = json.loads((STORE/'catalog.json').read_text())
    assert catalog['checkpoints']['chunked']['backend']=='chunked' and catalog['checkpoints']['git']['backend']=='git'
    git_id = catalog['checkpoints']['git']['manifest']
    sql('main',"ALTER TABLE items ADD COLUMN flag bool DEFAULT true; UPDATE items SET value='changed'")
    cli('branch','--from','git','--name','other'); cli('start','other')
    assert sql('other',"SELECT value FROM items WHERE id=1")=='original'
    sql('other',"UPDATE items SET value='independent' WHERE id=1")
    assert sql('main',"SELECT value FROM items WHERE id=1")=='changed'
    assert sql('other',"SELECT current_setting('fsync'),current_setting('synchronous_commit'),current_setting('full_page_writes'),current_setting('data_checksums')")=='on|on|on|on'
    cli('stop','other')
    # Portable export stays format 1 and imports into a normal independently owned branch.
    cli('export','git','--output',ROOT/'bundle')
    assert json.loads((ROOT/'bundle/manifest.json').read_text())['format']==1
    cli('restore',ROOT/'bundle','--name','imported'); cli('start','imported')
    assert sql('imported',"SELECT value FROM items WHERE id=1")=='original'
    cli('stop','imported')
    for fault in ('git_objects_synced','git_ref_published','git_manifest_published'):
        cli('checkpoint','main','--name',fault.replace('_','-'),code=99,fault=fault)
        assert state('checkpoints',fault.replace('_','-'))=='failed'
        assert state('branches','main')=='running'
    cli('stop','main')
    cli('checkpoint','main','--name','parked-interruption',code=99,fault='git_ref_published')
    assert state('branches','main')=='stopped'
    cli('start','main')
    interrupted_export=ROOT/'interrupted-export'
    cli('export','git','--output',interrupted_export,code=99,fault='export_before_publish')
    assert not interrupted_export.exists()
    cli('--no-cache','branch','--from','git','--name','interrupted',code=99,fault='restore_during_write')
    assert state('branches','interrupted')=='failed'
    # Local config and alternates are refused before a subprocess reads either.
    repo = STORE/'git-store/repo'
    cfg = (repo/'config').read_bytes()
    (repo/'config').write_bytes(cfg+b'[include]\npath = /invalid\n')
    assert cli('branch','--from','git','--name','bad-config',code=1)['error']['code']=='unsafe_git'
    (repo/'config').write_bytes(cfg)
    (repo/'objects/info/alternates').write_text('/invalid\n')
    assert cli('branch','--from','git','--name','bad-alternates',code=1)['error']['code']=='unsafe_git'
    (repo/'objects/info/alternates').unlink()
    # Warm cache must not hide a corrupted committed manifest/tree.
    manifest_path = STORE/'git-store/manifests'/f'{git_id}.json'
    raw = manifest_path.read_bytes(); manifest = json.loads(raw)
    manifest_path.write_bytes(raw+b' ')
    assert cli('branch','--from','git','--name','bad-manifest',code=1)['error']['code']=='integrity'
    manifest_path.write_bytes(raw)
    blob = next(f['git_blob'] for f in manifest['files'] if f['path']=='PG_VERSION')
    blob_path = repo/'objects'/blob[:2]/blob[2:]
    saved = blob_path.read_bytes(); blob_path.chmod(0o600); blob_path.write_bytes(b'corrupt')
    assert not cli('--no-cache','branch','--from','git','--name','bad-blob',code=1)['ok']
    blob_path.write_bytes(saved)
    # Both backends still restore, packing is explicit, and retained objects survive it.
    cli('reset','main','--to','chunked','--yes'); cli('start','main')
    assert sql('main',"SELECT value FROM items WHERE id=1")=='original'
    cli('reset','main','--to','git','--yes'); cli('start','main')
    assert sql('main',"SELECT value FROM items WHERE id=1")=='original'
    # Removing an unpinned capture forgets its parentless ref; interruption is retryable.
    sql('main',"UPDATE items SET value='disposable' WHERE id=2")
    cli('checkpoint','main','--name','discard')
    discard_id=json.loads((STORE/'catalog.json').read_text())['checkpoints']['discard']['manifest']
    cli('delete-checkpoint','discard','--yes')
    assert not cli('delete-checkpoint','git','--yes',code=1)['ok']
    cli('gc','--yes',code=99,fault='git_gc_ref_removed')
    cli('gc','--yes')
    refs=subprocess.check_output(['git','--git-dir',str(repo),'for-each-ref','--format=%(refname)'],text=True)
    assert discard_id not in refs and git_id in refs
    cli('start','imported')
    assert sql('imported',"SELECT value FROM items WHERE id=1")=='original'
    cli('stop','imported')
    cli('--no-cache','branch','--from','git','--name','packed'); cli('start','packed')
    assert sql('packed',"SELECT value FROM items WHERE id=1")=='original'
    cli('stop','packed'); cli('stop','main')
    (ROOT/'SUCCESS').write_text('mixed backends, isolation, export, corruption, hardening, interruption and packed restore passed\n')
    print(ROOT)
finally:
    for data in (STORE/'generations').glob('*/data'):
        pid = data/'postmaster.pid'
        if pid.exists() and pid.read_text().splitlines()[1]==str(data):
            subprocess.run([str(PG/'pg_ctl'),'-D',str(data),'-m','fast','-w','stop'],capture_output=True)
    (ROOT/'test-results.json').write_text(json.dumps(records,indent=2,default=str)+'\n')
    assert not list(ROOT.rglob('postmaster.pid'))
