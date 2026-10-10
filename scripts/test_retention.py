#!/usr/bin/env python3
"""Real PostgreSQL retention: ownership, borrowing, shared packs and crash recovery."""
import json
import os
from pathlib import Path
import subprocess
import tempfile

BIN = Path(os.environ.get('FORKLIFT_BIN', 'target/debug/forklift')).resolve()
PG = Path(os.environ.get('PG_BIN', '/opt/homebrew/opt/postgresql@17/bin'))
Path('benchmark-data').mkdir(exist_ok=True)
ROOT = Path(tempfile.mkdtemp(prefix='retention-', dir='benchmark-data')).resolve()
STORE = ROOT/'store'


def cli(*args, code=None, fault=None, pair_fault=None, project_fault=None):
    env = {k: v for k, v in os.environ.items() if k not in ['FORKLIFT_FAILPOINT', 'FORKLIFT_PAIR_TEST_FAILPOINT', 'FORKLIFT_PROJECT_TEST_FAILPOINT']}
    if fault: env['FORKLIFT_FAILPOINT'] = fault
    if pair_fault: env['FORKLIFT_PAIR_TEST_FAILPOINT'] = pair_fault
    if project_fault: env['FORKLIFT_PROJECT_TEST_FAILPOINT'] = project_fault
    p = subprocess.run([str(BIN), '--json', '--pg-bin', str(PG), '--home', str(STORE), *map(str, args)],
                       text=True, capture_output=True, env=env, timeout=180)
    if fault or pair_fault or project_fault:
        assert p.returncode == 99, (args, p.stdout, p.stderr)
        return
    obj = json.loads(p.stdout)
    if code:
        assert not obj['ok'] and obj['error']['code'] == code, (args, obj)
        return
    assert p.returncode == 0 and obj['ok'], (args, obj, p.stderr)
    return obj['result']


def cat(): return json.loads((STORE/'catalog.json').read_text())
def pair(w, *args, **kw): return cli('pair', w, *args, **kw)
def sql(branch, query):
    conn = cli('connection', branch)['connection']
    return subprocess.check_output([str(PG/'psql'), conn, '-XAt', '-v', 'ON_ERROR_STOP=1', '-c', query], text=True).strip()
def current(w): return json.loads((w/'current.json').read_text())['branch']
def init_pair(w, checkpoint): pair(w, 'init', '--source', source, '--store', STORE, '--checkpoint', checkpoint)

try:
    cli('init', 'seed'); cli('start', 'seed')
    sql('seed', "CREATE TABLE retained(value text); INSERT INTO retained VALUES ('baseline')")
    cli('checkpoint', 'seed', '--name', 'baseline'); cli('stop', 'seed')
    source = ROOT/'source'; source.mkdir(); (source/'app.txt').write_text('baseline')
    w = ROOT/'pair'; init_pair(w, 'baseline'); pair(w, 'capture', 'keep')
    import shutil
    copied = ROOT/'copied'; shutil.copytree(w, copied)
    pair(copied, 'prune', '--yes', code='pair_metadata')
    keep = json.loads((w/'pairs/keep.json').read_text())['checkpoint']
    print('repeated rewinds reclaim obsolete branches and source', flush=True)
    for _ in range(3):
        pair(w, 'restore', 'keep')
    before = (STORE/'catalog.json').read_bytes()
    plan = pair(w, 'prune')
    assert len(plan['branches']) == 3 and len(plan['source_paths']) == 3, plan
    assert (STORE/'catalog.json').read_bytes() == before
    pair(w, 'prune', '--yes')
    assert len(list((w/'code').iterdir())) == 1
    assert len(cat()['branches']) == 2
    assert sql(current(w), 'SELECT value FROM retained') == 'baseline'
    cli('delete-branch', current(w), '--yes', code='referenced')
    cli('delete-checkpoint', keep, '--yes', code='referenced')

    print('shared captures and borrowed checkpoints remain restorable', flush=True)
    pair(w, 'capture', 'discard')
    discarded = json.loads((w/'pairs/discard.json').read_text())['checkpoint']
    manifest = json.loads((STORE/'objects/manifests'/f"{cat()['checkpoints'][discarded]['manifest']}.json").read_text())
    shared = {v['pack'] for v in manifest['chunks'].values()}
    borrower = ROOT/'borrower'; init_pair(borrower, discarded)
    pair(w, 'forget', 'discard'); assert (w/'pairs/discard.json').exists()
    pair(w, 'forget', 'discard', '--yes', pair_fault='forget_unpublished')
    assert not (w/'pairs/discard.json').exists()
    retained = pair(w, 'prune', '--yes')
    assert discarded in cat()['checkpoints'] and any(x['name'] == discarded for x in retained['retained'])
    pair(borrower, 'capture', 'own'); pair(borrower, 'restore', 'own'); pair(borrower, 'prune', '--yes')
    pair(w, 'prune', '--yes')
    assert discarded not in cat()['checkpoints']
    assert any((STORE/'objects/packs'/f'{p}.pack').exists() for p in shared)
    pair(w, 'restore', 'keep'); assert sql(current(w), 'SELECT value FROM retained') == 'baseline'
    pair(w, 'prune', '--yes')

    print('source links are refused before pruning any branch', flush=True)
    pair(w, 'restore', 'keep')
    obsolete = next(p for p in (w/'code').iterdir() if str(p.relative_to(w)) != json.loads((w/'current.json').read_text())['code'])
    sentinel = ROOT/'sentinel'; sentinel.write_text('untouched')
    link = obsolete/'link'; link.symlink_to(sentinel)
    before = (STORE/'catalog.json').read_bytes()
    pair(w, 'prune', '--yes', code='unsafe_path')
    assert before == (STORE/'catalog.json').read_bytes() and sentinel.read_text() == 'untouched'
    link.unlink(); os.link(sentinel, link); pair(w, 'prune', '--yes', code='unsafe_path'); link.unlink()
    pair(w, 'prune', '--yes', pair_fault='prune_source_removed')
    pair(w, 'prune', '--yes')

    print('interrupted metadata deletion and GC resume with live packs intact', flush=True)
    cli('init', 'temporary'); cli('start', 'temporary')
    sql('temporary', "CREATE TABLE unique_data AS SELECT repeat(md5(i::text),100) AS value FROM generate_series(1,2000) i")
    cli('checkpoint', 'temporary', '--name', 'temporary_cp'); cli('stop', 'temporary')
    cli('delete-branch', 'temporary'); assert 'temporary' in cat()['branches']
    cli('delete-branch', 'temporary', '--yes', fault='retention_unpublished')
    assert 'temporary' not in cat()['branches']
    cli('delete-checkpoint', 'temporary_cp', '--yes')
    plan = cli('gc'); assert plan['manifests'] and plan['packs']
    dead_pack = Path(plan['packs'][0]); original_mode = dead_pack.stat().st_mode & 0o777
    dead_pack.chmod(0o666); cli('gc', '--yes', code='unsafe_path'); dead_pack.chmod(original_mode)
    held_pack = ROOT/'held.pack'; dead_pack.rename(held_pack); dead_pack.symlink_to(held_pack)
    cli('gc', '--yes', code='unsafe_path'); dead_pack.unlink(); held_pack.rename(dead_pack)
    cli('gc', '--yes', fault='gc_manifests_removed'); cli('gc', '--yes', fault='gc_pack_removed'); cli('gc', '--yes')
    cli('cleanup', '--yes')
    pair(w, 'restore', 'keep'); assert sql(current(w), 'SELECT value FROM retained') == 'baseline'
    pair(w, 'prune', '--yes')
    # Simulate a pre-retention entry: no inference from names or directory layout.
    cli('init', 'legacy'); c = cat(); del c['branches']['legacy']['retention_safe']
    (STORE/'catalog.json').write_text(json.dumps(c))
    cli('delete-branch', 'legacy', '--yes', code='referenced')
    # Failed before generation allocation can be deleted without guessing about files.
    cli('init', 'missing_generation'); c = cat(); generation = STORE/'generations'/c['branches']['missing_generation']['head']['id']
    import shutil
    shutil.rmtree(generation); c['branches']['missing_generation']['head']['state'] = 'failed'
    (STORE/'catalog.json').write_text(json.dumps(c)); cli('delete-branch', 'missing_generation', '--yes')
    print('project prune bounds mirror/recovery copies and blocks pending recovery', flush=True)
    pair(w, 'stop'); pair(borrower, 'stop')
    project = ROOT/'project'; project.mkdir(); (project/'app.txt').write_text('original')
    (project/'.forklift.json').write_text(json.dumps({'format': 1, 'source': ['app.txt'], 'connection_env': 'DATABASE_URL', 'commands': {}}))
    subprocess.run(['git', 'init', str(project)], check=True, capture_output=True)
    def proj(*args, **kw): return cli('project', '--root', project, *args, **kw)
    cli('start', 'seed'); cli('checkpoint', 'seed', '--name', 'configured_seed'); cli('stop', 'seed')
    proj('configure', '--store', STORE, '--checkpoint', 'configured_seed')
    cli('delete-checkpoint', 'configured_seed', '--yes', code='referenced')
    # New pin survives a crash before host publication; successful retry releases old pin.
    proj('configure', '--store', STORE, '--checkpoint', 'baseline', project_fault='configure_pinned')
    cli('delete-checkpoint', 'configured_seed', '--yes', code='referenced')
    proj('configure', '--store', STORE, '--checkpoint', 'baseline')
    cli('delete-checkpoint', 'configured_seed', '--yes')
    proj('setup'); proj('capture', 'keep')
    for _ in range(3):
        (project/'app.txt').write_text('changed'); proj('restore', 'keep')
        proj('prune', '--yes')
        assert (project/'app.txt').read_text() == 'original'
    state = Path(proj('inspect')['state'])
    assert len(list((state/'pair/code').iterdir())) == 1
    assert not any(p.name.startswith(('source-', 'verify-', 'preflight-')) for p in state.iterdir())
    # A pair command must not bypass the enclosing project's pending recovery.
    (state/'pending.json').write_text('{}')
    pair(state/'pair', 'prune', '--yes', code='project_pending')
    proj('prune', '--yes', code='project_pending'); (state/'pending.json').unlink()
    proj('stop')
    cli('start', 'seed'); cli('checkpoint', 'seed', '--name', 'future_seed'); cli('stop', 'seed')
    proj('configure', '--store', STORE, '--checkpoint', 'future_seed')
    proj('unconfigure'); cli('delete-checkpoint', 'future_seed', '--yes', code='referenced')
    proj('unconfigure', '--yes', project_fault='unconfigure_unpublished')
    cli('delete-checkpoint', 'future_seed', '--yes', code='referenced')
    proj('unconfigure', '--yes'); cli('delete-checkpoint', 'future_seed', '--yes')
    proj('inspect')  # existing binding is independent of the future-setup seed
    print('retention passed:', ROOT, flush=True)
finally:
    if (STORE/'catalog.json').exists():
        for name in cat()['branches']:
            subprocess.run([str(BIN), '--home', str(STORE), '--pg-bin', str(PG), 'stop', name], capture_output=True)
