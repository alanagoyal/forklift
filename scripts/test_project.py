#!/usr/bin/env python3
"""Real Git worktrees and PostgreSQL; deterministic tests, not an agent demo."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

REPO = Path(__file__).resolve().parents[1]
BIN = Path(os.environ.get('FORKLIFT_BIN', REPO/'target/debug/forklift')).resolve()
PG = Path(os.environ.get('PG_BIN', '/opt/homebrew/opt/postgresql@17/bin'))
(REPO/'benchmark-data').mkdir(exist_ok=True)
ROOT = Path(tempfile.mkdtemp(prefix='project-', dir=REPO/'benchmark-data'))
STORE = ROOT/'store'
records = []

def invoke(argv, cwd=None, expected=0, env=None):
    p = subprocess.run([str(x) for x in argv], cwd=cwd, env=env, capture_output=True, text=True, timeout=180)
    records.append({'argv': [str(x) for x in argv], 'cwd': str(cwd), 'exit': p.returncode, 'stdout': p.stdout, 'stderr': p.stderr})
    assert p.returncode == expected, (argv, p.returncode, p.stdout, p.stderr)
    return p.stdout

def db(*args):
    return json.loads(invoke([BIN, '--json', '--pg-bin', PG, '--home', STORE, *args]))['result']

def project(root, *args, expected=0, fault=None, pair_fault=None):
    env = os.environ.copy()
    if fault: env['FORKLIFT_PROJECT_TEST_FAILPOINT'] = fault
    if pair_fault: env['FORKLIFT_PAIR_TEST_FAILPOINT'] = pair_fault
    return invoke([BIN, '--json', '--pg-bin', PG, 'project', '--root', root, *args], expected=expected, env=env)

def sql(root, query):
    return project(root, 'run', '--', sys.executable, '-c',
        'import os,subprocess,sys; subprocess.run([os.environ["PG_BIN"]+"/psql",os.environ[sys.argv[1]],"-XAt","-v","ON_ERROR_STOP=1","-c",sys.argv[2]],check=True)',
        'APP_DB' if root.name.startswith('shell') else 'DATABASE_URL', query).strip()

def git(root, *args): return invoke(['git', '-C', root, *args]).strip()
def inspect(root): return json.loads(project(root, 'inspect'))['result']
def metadata(root):
    admin = Path(git(root, 'rev-parse', '--absolute-git-dir'))
    return {**{k: (admin/k).read_bytes() for k in ['HEAD', 'index', 'commondir']}, '.git': (root/'.git').read_bytes()}

try:
    db('init', 'seed')
    db('start', 'seed')
    connection = db('connection', 'seed')['uri']
    invoke([PG/'psql', connection, '-X', '-v', 'ON_ERROR_STOP=1', '-c',
            'CREATE TABLE items (id int PRIMARY KEY, name text); INSERT INTO items VALUES (1,\'one\'),(2,\'two\'),(3,\'three\');'])
    db('checkpoint', 'seed', '--name', 'development')
    db('stop', 'seed')
    roots = []
    for kind in ['python', 'shell']:
        repo = ROOT/(kind+'-repo'); repo.mkdir()
        if kind == 'python':
            path = 'src/app.py'
            code = 'import os,subprocess\nprint("v1", subprocess.check_output([os.environ["PG_BIN"]+"/psql",os.environ["DATABASE_URL"],"-XAtc","SELECT count(*) FROM items"],text=True).strip())\n'
            command = [sys.executable, path]
            sources = ['src', 'migrations', 'manifest.txt']
        else:
            path = 'tools/report.sh'
            code = '#!/usr/bin/env bash\nset -euo pipefail\nprintf "v1 "\n"$PG_BIN/psql" "$APP_DB" -XAtc "SELECT count(*) FROM items"\n'
            command = ['bash', path]
            sources = ['tools/report.sh', 'db/schema', 'manifest.txt']
        (repo/path).parent.mkdir(parents=True); (repo/path).write_text(code)
        (repo/path).chmod(0o751)
        (repo/'manifest.txt').write_text('original\n')
        config = {'format':1, 'source':sources, 'connection_env':'APP_DB' if kind=='shell' else 'DATABASE_URL', 'commands':{'verify':command, 'start':command}}
        (repo/'.forklift.json').write_text(json.dumps(config))
        for hook in ['.bb-env-setup.sh', '.bb-env-teardown.sh']:
            shutil.copy(REPO/'examples/project-hooks'/hook, repo/hook)
        git(repo,'init'); git(repo,'add','.'); git(repo,'-c','user.name=test','-c','user.email=test@example.test','commit','-m','fixture')
        project(repo,'configure','--store',str(STORE),'--checkpoint','development')
        work = ROOT/(kind+'-work'); git(repo,'worktree','add','-b','experiment',str(work)); roots.append(work)
        env = {k:v for k,v in os.environ.items() if not k.startswith('BB_') and k != 'NODE_ENV'}
        env['PATH'] = str(BIN.parent)+os.pathsep+env['PATH']
        invoke(['env','bash','.bb-env-setup.sh'],cwd=work,env=env)
        state = inspect(work)['state']
        invoke(['env','bash','.bb-env-setup.sh'],cwd=work,env=env)
        assert inspect(work)['state'] == state
        assert project(work,'task','verify').strip() == 'v1 3'
        (work/'manifest.txt').write_text('staged\n'); git(work,'add','manifest.txt')
        (work/'manifest.txt').write_text('dirty\n')
        (work/path).chmod(0o751)
        if kind == 'python': (work/'src/empty').mkdir()
        (work/'.env.local').write_text('preserved local value\n')
        (work/'node_modules').mkdir(); (work/'node_modules/cache').write_text('preserved\n')
        # A sibling of an individual owned path must also survive.
        (work/Path(path).parent/'outside.txt').write_text('untracked\n')
        before = metadata(work)
        project(work,'capture','baseline')
        (work/path).write_text(code.replace('v1', 'v2'))
        sql(work,'ALTER TABLE items ADD COLUMN archived boolean DEFAULT false; UPDATE items SET archived=true WHERE id=3; DELETE FROM items WHERE id=2;')
        assert project(work,'task','verify').strip() == 'v2 2'
        (work/'manifest.txt').write_text('later\n')
        added = work / ('migrations' if kind == 'python' else 'db/schema')
        added.mkdir(parents=True); (added/'change.sql').write_text('-- migration created after checkpoint\n')
        if kind=='python': (work/'src/new.txt').write_text('new file removed by restore\n')
        project(work,'restore','baseline')
        assert project(work,'task','verify').strip() == 'v1 3'
        assert sql(work,"SELECT count(*) FROM information_schema.columns WHERE table_name='items' AND column_name='archived'") == '0'
        assert (work/'manifest.txt').read_text()=='dirty\n'
        assert any(p.read_text() == code.replace('v1','v2') for p in Path(state,'pair/code').glob('*/'+path))
        assert not (work/'src/new.txt').exists()
        assert not added.exists()
        assert (work/path).stat().st_mode & 0o777 == 0o751
        if kind == 'python': assert (work/'src/empty').is_dir()
        assert metadata(work)==before
        assert (work/'.env.local').read_text()=='preserved local value\n'
        assert (work/'node_modules/cache').read_text()=='preserved\n'
        assert (work/Path(path).parent/'outside.txt').read_text()=='untracked\n'
        # BB-hook teardown, with the same sanitized environment used for setup.
        invoke(['env','bash','.bb-env-teardown.sh'],cwd=work,env=env)
        records.append({'scenario':kind,'state':state,'git_metadata_preserved':True,'baseline_restored':True})

    work = roots[0]; app = work/'src/app.py'; baseline = app.read_text()
    # Capacity refusal must leave live edits usable, without a recovery journal
    # or new retained source/database generations. Stopping the app alone does
    # not park its database: project stop supplies a slot for the retry.
    app.write_text(baseline + '\n# retained dirty work\n')
    sql(work, 'DELETE FROM items WHERE id=3')
    db('config', '--max-running', '1')
    before_pair = inspect(work)
    state = Path(before_pair['state'])
    before_paths = set(state.rglob('*'))
    before_branches = db('list')['branches']
    error = json.loads(project(work, 'restore', 'baseline', expected=1))['error']
    assert error['code'] == 'running_limit' and 'forklift project stop' in error['message']
    assert inspect(work) == before_pair and set(state.rglob('*')) == before_paths
    assert db('list')['branches'] == before_branches
    assert app.read_text() == baseline + '\n# retained dirty work\n'
    assert project(work, 'task', 'verify').strip() == 'v1 2'
    project(work, 'stop')
    project(work, 'restore', 'baseline')
    assert app.read_text() == baseline
    assert project(work, 'task', 'verify').strip() == 'v1 3'
    db('config', '--max-running', '3')
    for stage, pair_stage, committed in [
        ('restore_journal',None,False), (None,'restore_code',False),
        (None,'restore_database',False),(None,'restore_started',False),
        (None,'restore_published',True),('restore_pair',None,True),
        ('restore_removed',None,True),('restore_item',None,True),('restore_files',None,True)]:
        app.write_text(baseline.replace('v1','v2'))
        sql(work,'DELETE FROM items WHERE id=3')
        project(work,'restore','baseline',expected=99,fault=stage,pair_fault=pair_stage)
        project(work,'task','verify',expected=1)
        project(work,'recover')
        assert project(work,'task','verify').strip() == ('v1 3' if committed else 'v2 2')
        project(work,'recover')
        project(work,'restore','baseline')
        records.append({'interruption':stage or pair_stage,'committed':committed,'recovered':True})
    # Capture recovery doesn't replace current worktree edits.
    for stage in ['capture_code', 'capture_database', 'capture_published']:
        project(work,'capture',stage,expected=99,pair_fault=stage)
        project(work,'task','verify',expected=1)
        project(work,'recover')
        assert project(work,'task','verify').strip() == 'v1 3'
    # A directory/file type transition is restored within the owned root.
    shutil.rmtree(work/'src'); (work/'src').write_text('directory replaced by file')
    project(work,'restore','baseline')
    assert project(work,'task','verify').strip() == 'v1 3'
    # Corrupt selected recovery source: fail closed before touching worktree files.
    project(work,'restore','baseline',expected=99,fault='restore_pair')
    state = Path(inspect(roots[1])['state'])  # other worktree remains accessible
    admin = Path(git(work,'rev-parse','--absolute-git-dir'))
    common = Path(git(work,'rev-parse','--path-format=absolute','--git-common-dir'))
    own = common/'forklift-workspaces'/json.loads((admin/'forklift-id.json').read_text())
    current = json.loads((own/'pair/current.json').read_text())
    saved = own/'pair'/current['code']/'src/app.py'; original = saved.read_bytes(); saved.write_text('corrupt')
    project(work,'recover',expected=1); saved.write_bytes(original); project(work,'recover')
    # Selected symlink cannot escape into excluded files.
    app.unlink(); app.symlink_to(work/'.env.local')
    project(work,'capture','unsafe',expected=1)
    project(work,'restore','baseline',expected=1)
    assert (work/'.env.local').read_text()=='preserved local value\n'
    app.unlink(); app.write_text(baseline)
    # Live managed command excludes checkpoint/restore.
    child = subprocess.Popen([str(BIN),'--pg-bin',str(PG),'project','--root',str(work),'run','--',sys.executable,'-c',
        'import pathlib,time; pathlib.Path("ready").write_text("1"); time.sleep(30)'], start_new_session=True)
    try:
        for _ in range(100):
            if (work/'ready').exists(): break
            time.sleep(.1)
        assert (work/'ready').exists()
        project(work,'capture','busy',expected=1)
    finally:
        import signal
        os.killpg(child.pid,signal.SIGTERM); child.wait(timeout=10)
    assert project(roots[1],'task','verify').strip()=='v1 3'
    # Seed database is unchanged as well.
    db('start','seed')
    assert invoke([PG/'psql',db('connection','seed')['uri'],'-XAtc','SELECT count(*) FROM items']).strip()=='3'
    db('stop','seed')
    # Deleting a Git worktree retains its externally stored checkpoints; new worktree gets new identity.
    old_state = inspect(roots[1])['state']
    project(roots[1],'stop')
    shell_repo = ROOT/'shell-repo'
    git(shell_repo,'worktree','remove','--force',str(roots[1]))
    assert Path(old_state,'pair/pairs/baseline.json').exists()
    git(shell_repo,'worktree','add',str(roots[1]),'experiment')
    project(roots[1],'setup',expected=99,fault='setup_ready')
    project(roots[1],'setup')
    assert inspect(roots[1])['state'] != old_state
finally:
    try:
        for branch in db('list')['branches']:
            if branch['state'] != 'failed': db('stop',branch['name'])
        cleanup = db('list')
        (ROOT/'cleanup.json').write_text(json.dumps(cleanup,indent=2))
        assert cleanup['active_clusters'] == 0
        assert not list(STORE.glob('generations/*/data/postmaster.pid'))
    finally:
        (ROOT/'records.json').write_text(json.dumps(records,indent=2))
        print(ROOT)

(ROOT/'SUCCESS').write_text(f"two project layouts, {sum(r.get('exit') == 99 for r in records)} injected exits, isolation, Git preservation, corruption and gate checks passed\n")
