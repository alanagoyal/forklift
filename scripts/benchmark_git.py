#!/usr/bin/env python3
"""Disposable, local database history comparison. See docs/git-benchmark.md."""
import argparse
import contextlib
import gzip
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import time

REPO = Path(__file__).resolve().parents[1]
METHODS = ['forklift', 'dump-git', 'dump-lfs', 'physical-git', 'physical-lfs']


def digest(data):
    return hashlib.sha256(data).hexdigest()


def usage(path):
    files = [p for p in path.rglob('*') if p.is_file() and not p.is_symlink()] if path.exists() else []
    return {'files': len(files), 'apparent_bytes': sum(p.stat().st_size for p in files)}


class Experiment:
    def __init__(self, a):
        self.a = a
        self.root = a.root.resolve()
        self.out = a.output.resolve()
        self.root.mkdir(parents=True, exist_ok=False)
        self.root.chmod(0o700)
        self.out.mkdir(parents=True, exist_ok=False)
        self.env = {k: v for k, v in os.environ.items() if k in ('PATH', 'HOME', 'TMPDIR', 'LANG')}
        self.env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL='/dev/null',
                        GIT_TERMINAL_PROMPT='0', PYTHONDONTWRITEBYTECODE='1')
        self.env['PATH'] = str(a.lfs_bin.resolve().parent) + ':' + self.env.get('PATH', '')
        self.sockets = Path(tempfile.mkdtemp(prefix='fgit-'))
        self.servers = set()
        self.stores = set()
        self.context = {'operation': 'setup'}
        self.records = []
        self.stage_name = 'setup'
        self.stages = {}
        self.synced_lfs = set()

    def log(self, file, value):
        with gzip.open(self.out / file, 'at') as f:
            f.write(json.dumps(value) + '\n')

    def run(self, argv, cwd=None, extra=None):
        started = time.perf_counter()
        p = subprocess.run(list(map(str, argv)), cwd=cwd, env=self.env | (extra or {}),
                           capture_output=True, timeout=1200)
        self.log('commands.jsonl.gz', self.context | {'argv': list(map(str, argv)),
            'cwd': str(cwd) if cwd else None, 'stage': self.stage_name,
            'elapsed_s': time.perf_counter()-started, 'exit': p.returncode,
            'stdout_sha256': digest(p.stdout), 'stdout_bytes': len(p.stdout),
            'stderr': p.stderr.decode(errors='replace')})
        if p.returncode:
            raise RuntimeError(f'{argv}: {p.stderr.decode()} {p.stdout.decode()}')
        return p.stdout.decode().strip()

    @contextlib.contextmanager
    def stage(self, name):
        previous = self.stage_name
        self.stage_name = name
        start = time.perf_counter()
        try:
            yield
        finally:
            self.stages[name] = self.stages.get(name, 0) + time.perf_counter()-start
            self.stage_name = previous

    def git(self, repo, *args):
        return self.run(['git', '-C', repo, *args])

    def fk(self, store, *args):
        self.stores.add(store)
        return json.loads(self.run([self.a.bin.resolve(), '--json', '--pg-bin', self.a.pg_bin,
            '--copy-mode', 'apfs', '--home', store, *args], extra={'FORKLIFT_TIMING': '1'}))['result']

    def sql(self, uri, query):
        return self.run([self.a.pg_bin/'psql', uri, '-XAt', '-v', 'ON_ERROR_STOP=1', '-c', query])

    def init(self, data):
        self.run([self.a.pg_bin/'initdb', '-D', data, '--data-checksums', '--no-locale', '-E', 'UTF8', '-A', 'trust'])

    def start(self, data):
        key = digest(str(data).encode())[:12]
        socket = self.sockets/key
        socket.mkdir(exist_ok=True)
        self.servers.add(data)
        self.run([self.a.pg_bin/'pg_ctl', '-D', data, '-l', data.parent/(data.name+'.log'),
                  '-o', f"-k {socket} -h '' -p 5432 -c fsync=on -c synchronous_commit=on -c full_page_writes=on", '-w', 'start'])
        return f'host={socket} port=5432 dbname=postgres'

    def stop(self, data):
        if (data/'postmaster.pid').exists():
            self.run([self.a.pg_bin/'pg_ctl', '-D', data, '-m', 'fast', '-w', 'stop'])
        self.servers.discard(data)

    def connection(self, method, base):
        return self.fk(base/'store', 'connection', 'main')['uri'] if method == 'forklift' else self.uri

    def verify(self, repo, uri):
        assert self.sql(uri, "SELECT current_setting('fsync'),current_setting('synchronous_commit'),current_setting('full_page_writes'),current_setting('data_checksums')") == 'on|on|on|on'
        schema = self.run([self.a.pg_bin/'pg_dump', '--dbname', uri, '--schema-only', '--no-owner', '--no-privileges'])
        schema = '\n'.join(s for s in schema.splitlines() if not s.startswith(('\\restrict ', '\\unrestrict ')))
        rows = self.sql(uri, "SELECT md5(string_agg(row_to_json(t)::text, E'\\n' ORDER BY id)) FROM benchmark_private.payload t")
        app = json.loads(self.run([sys.executable, repo/'src/app.py'],
            extra={'DATABASE_URL': uri, 'PG_BIN': str(self.a.pg_bin)}))
        source = {str(p.relative_to(repo/'src')): digest(p.read_bytes()) for p in sorted((repo/'src').rglob('*')) if p.is_file()}
        return {'schema': digest(schema.encode()), 'rows': rows, 'app': app, 'source': source}

    def commit(self, repo, name):
        self.git(repo, 'add', '-A')
        self.git(repo, 'commit', '--quiet', '--allow-empty', '-m', name)
        if (repo/'.git/lfs/objects').exists():
            # LFS clean creates immutable local objects. Explicitly persist new
            # objects/directories; Git core.fsync does not cover the LFS store.
            with self.stage('lfs_sync'):
                directories = set()
                for obj in (repo/'.git/lfs/objects').rglob('*'):
                    if obj.is_file() and obj not in self.synced_lfs:
                        with obj.open('rb') as f:
                            os.fsync(f.fileno())
                        self.synced_lfs.add(obj)
                        parent = obj.parent
                        while parent != repo:
                            directories.add(parent)
                            parent = parent.parent
                for directory in sorted(directories, key=lambda p: len(p.parts), reverse=True):
                    fd = os.open(directory, os.O_RDONLY)
                    try:
                        os.fsync(fd)
                    finally:
                        os.close(fd)
        return self.git(repo, 'rev-parse', 'HEAD')

    def copy(self, source, dest):
        # Native APFS clone copies for every physical path, including baselines.
        self.run(['/bin/cp', '-cR', source, dest])

    def materialize(self, method, repo, data):
        if method.startswith('dump'):
            with self.stage('initdb'):
                self.init(data)
            with self.stage('start'):
                uri = self.start(data)
            with self.stage('import'):
                self.run([self.a.pg_bin/'pg_restore', '--exit-on-error', '--dbname', uri, repo/'snapshot.dump'])
        else:
            with self.stage('copy_physical'):
                self.copy(repo/'data', data)
                for rel, mode in json.loads((repo/'directories.json').read_text()):
                    p = data/rel
                    p.mkdir(parents=True, exist_ok=True)
                    p.chmod(mode)
                data.chmod(0o700)
            with self.stage('sync_physical'):
                self.run([self.a.pg_bin/'initdb', '--sync-only', '-D', data])
            with self.stage('start'):
                uri = self.start(data)
        return uri

    def capture(self, method, base, repo, uri, name):
        if method == 'forklift':
            with self.stage('checkpoint'):
                self.fk(base/'store', 'checkpoint', 'main', '--name', name)
        elif method.startswith('dump'):
            with self.stage('dump'):
                self.run([self.a.pg_bin/'pg_dump', '--dbname', uri, '-Fc', '-Z', '6', '-f', repo/'snapshot.dump'])
        else:
            with self.stage('stop'):
                self.stop(base/'live')
            with self.stage('clean_state'):
                control = self.run([self.a.pg_bin/'pg_controldata', base/'live'])
                assert next(line.split(':', 1)[1].strip() for line in control.splitlines() if line.startswith('Database cluster state:')) == 'shut down'
            with self.stage('copy_physical'):
                if (repo/'data').exists():
                    shutil.rmtree(repo/'data')
                self.copy(base/'live', repo/'data')
                dirs = [(str(p.relative_to(repo/'data')), p.stat().st_mode & 0o777)
                        for p in sorted((repo/'data').rglob('*')) if p.is_dir()]
                (repo/'directories.json').write_text(json.dumps(dirs))
            with self.stage('restart'):
                self.uri = self.start(base/'live')
        with self.stage('git_commit'):
            commit = self.commit(repo, name)
        if 'lfs_sync' in self.stages:
            self.stages['git_commit'] -= self.stages['lfs_sync']
        with self.stage('verify'):
            observed = self.verify(repo, self.connection(method, base))
        return commit, observed

    def timed(self, operation, action):
        self.context['operation'] = operation
        self.stages = {}
        load = os.getloadavg()
        start = time.perf_counter()
        value = action()
        record = self.context | {'elapsed_s': time.perf_counter()-start, 'stages_s': self.stages.copy(),
                                'loadavg_before': load, 'loadavg_after': os.getloadavg(), 'verified': True}
        self.records.append(record)
        self.log('samples.jsonl.gz', record)
        print(f"{self.context}: {record['elapsed_s']:.3f}s", flush=True)
        return value

    def storage(self, method, base, repo, point):
        value = self.context | {'point': point, 'total': usage(base), 'git': usage(repo/'.git'),
            'git_objects': usage(repo/'.git/objects'), 'lfs_objects': usage(repo/'.git/lfs/objects'),
            'snapshot_working_copy': usage(repo/'data') if method.startswith('physical') else
                {'apparent_bytes': (repo/'snapshot.dump').stat().st_size if (repo/'snapshot.dump').exists() else 0},
            'live': usage(base/'live'), 'forklift_store': usage(base/'store')}
        if method == 'forklift':
            value['forklift_stats'] = self.fk(base/'store', 'stats')
        value['git_count_objects'] = self.git(repo, 'count-objects', '-v')
        self.log('storage.jsonl.gz', value)

    def workflow(self, size, method):
        self.context = {'size_mib': size, 'method': method, 'operation': 'setup'}
        base = self.root/f'{size}-{method}'
        repo = base/'repo'
        repo.mkdir(parents=True)
        setup_start = time.perf_counter()
        self.git(repo, 'init', '--quiet')
        for key, value in [('user.name', 'benchmark'), ('user.email', 'benchmark@example.test'),
                           ('gc.auto', '0'), ('core.compression', '6'), ('core.fsync', 'committed'),
                           ('core.fsyncMethod', 'fsync')]:
            self.git(repo, 'config', key, value)
        if method.endswith('lfs'):
            self.git(repo, 'lfs', 'install', '--local')
            self.git(repo, 'lfs', 'track', 'data/**' if method.startswith('physical') else '*.dump')
        (repo/'src').mkdir()
        shutil.copyfile(REPO/'fixtures/invoice-app/app.py', repo/'src/app.py')
        for i in range(64):
            (repo/'src'/f'file-{i}.txt').write_text(hashlib.sha256(str(i).encode()).hexdigest()*256)
        if method == 'forklift':
            self.fk(base/'store', 'init', 'main')
            self.fk(base/'store', 'start', 'main')
            uri = self.connection(method, base)
        else:
            self.init(base/'live')
            uri = self.uri = self.start(base/'live')
        self.run([self.a.pg_bin/'psql', uri, '-X', '-v', 'ON_ERROR_STOP=1', '-f', REPO/'fixtures/invoice-app/seed.sql'])
        self.sql(uri, f"CREATE SCHEMA benchmark_private; CREATE TABLE benchmark_private.payload(id integer PRIMARY KEY, value text); INSERT INTO benchmark_private.payload SELECT i, (SELECT string_agg(md5(i::text||':'||j::text),'') FROM generate_series(1,64) j) FROM generate_series(1,{size*512}) i;")
        expected = self.verify(repo, uri)
        self.log('setup.jsonl.gz', self.context | {'elapsed_s': time.perf_counter()-setup_start,
                 'database_bytes': self.sql(uri, 'SELECT pg_database_size(current_database())'), 'state': expected})
        snapshots = []
        for phase in ['initial', 'unchanged', 'rows', 'schema']:
            for sample in range(1 if phase == 'initial' else self.a.samples):
                self.context = {'size_mib': size, 'method': method, 'phase': phase, 'sample': sample, 'operation': 'edit'}
                uri = self.connection(method, base)
                if phase == 'rows':
                    self.sql(uri, f"UPDATE benchmark_private.payload SET value=md5(id::text||':edit:{sample}')||substring(value from 33) WHERE id BETWEEN 1 AND 10")
                    (repo/'src/state.txt').write_text(f'row-edit-{sample}\n')
                elif phase == 'schema':
                    self.sql(uri, f'ALTER TABLE benchmark_private.payload ADD COLUMN tag_{sample} integer NOT NULL DEFAULT {sample}; CREATE INDEX tag_idx_{sample} ON benchmark_private.payload(tag_{sample})')
                    (repo/'src/state.txt').write_text(f'schema-edit-{sample}\n')
                expected = self.verify(repo, uri)
                self.log('states.jsonl.gz', self.context | {'state': expected})
                name = f'{phase}-{sample}'
                def capture():
                    commit, observed = self.capture(method, base, repo, uri, name)
                    assert observed == expected
                    return commit
                commit = self.timed('capture', capture)
                self.storage(method, base, repo, 'after_capture')
                snapshots.append((phase, sample, name, commit, expected))
        # Restore historical states only after all captures: unchanged capture samples
        # have no intervening user writes, restore, or independence probes.
        for phase, sample, name, commit, expected in snapshots:
            self.context = {'size_mib': size, 'method': method, 'phase': phase, 'sample': sample, 'operation': 'poison'}
            # Force actual source/data rewind, rather than a no-op checkout.
            self.context['operation'] = 'poison'
            self.sql(self.connection(method, base), "UPDATE benchmark_private.payload SET value='poison' WHERE id=1")
            (repo/'src/file-0.txt').write_text('poison')
            def restore():
                with self.stage('checkout_source'):
                    self.git(repo, 'restore', '--source', commit, '--worktree', 'src')
                if method == 'forklift':
                    with self.stage('reset'):
                        self.fk(base/'store', 'reset', 'main', '--to', name, '--yes')
                    with self.stage('start'):
                        self.fk(base/'store', 'start', 'main')
                else:
                    with self.stage('stop_remove'):
                        self.stop(base/'live')
                        shutil.rmtree(base/'live')
                    with self.stage('checkout_snapshot'):
                        snapshot = repo/('snapshot.dump' if method.startswith('dump') else 'data')
                        if snapshot.is_dir():
                            shutil.rmtree(snapshot)
                        else:
                            snapshot.unlink()
                        self.git(repo, 'restore', '--source', commit, '--worktree', snapshot.name, *([] if method.startswith('dump') else ['directories.json']))
                    self.uri = self.materialize(method, repo, base/'live')
                with self.stage('verify'):
                    assert self.verify(repo, self.connection(method, base)) == expected
            self.timed('restore', restore)
            sibling = base/'second'
            def provision():
                with self.stage('worktree'):
                    self.git(repo, 'worktree', 'add', '--detach', sibling, commit)
                if method == 'forklift':
                    with self.stage('branch'):
                        self.fk(base/'store', 'branch', '--from', name, '--name', 'second')
                    with self.stage('start'):
                        self.fk(base/'store', 'start', 'second')
                        other_uri = self.fk(base/'store', 'connection', 'second')['uri']
                else:
                    other_uri = self.materialize(method, sibling, base/'second-live')
                with self.stage('verify_independence'):
                    assert self.verify(sibling, other_uri) == expected
                    assert other_uri != self.connection(method, base)
                    self.sql(other_uri, "UPDATE benchmark_private.payload SET value='independent' WHERE id=1")
                    (sibling/'src/file-0.txt').write_text('independent')
                    assert self.sql(other_uri, 'SELECT value FROM benchmark_private.payload WHERE id=1') == 'independent'
                    assert self.verify(repo, self.connection(method, base)) == expected
                    self.sql(self.connection(method, base), "UPDATE benchmark_private.payload SET value='main-only' WHERE id=2")
                    assert self.sql(other_uri, "SELECT value <> 'main-only' FROM benchmark_private.payload WHERE id=2") == 't'
                    # Undo independence probe, preserving the workload's deterministic state.
                    self.sql(self.connection(method, base), "UPDATE benchmark_private.payload SET value=(SELECT string_agg(md5('2:'||j::text),'' ) FROM generate_series(1,64) j) WHERE id=2")
                    if phase in ('rows', 'schema'):
                        edit = sample if phase == 'rows' else self.a.samples-1
                        self.sql(self.connection(method, base), f"UPDATE benchmark_private.payload SET value=md5('2:edit:{edit}')||substring(value from 33) WHERE id=2")
                    assert self.verify(repo, self.connection(method, base)) == expected
            self.timed('provision', provision)
            self.storage(method, base, repo, 'with_second')
            self.context['operation'] = 'cleanup_second'
            if method == 'forklift':
                self.fk(base/'store', 'stop', 'second')
                self.fk(base/'store', 'delete-branch', 'second', '--yes')
            else:
                self.stop(base/'second-live')
                shutil.rmtree(base/'second-live')
            self.git(repo, 'worktree', 'remove', '--force', sibling)
        self.context['operation'] = 'maintenance'
        self.storage(method, base, repo, 'before_maintenance')
        def maintain():
            with self.stage('git_gc'):
                self.git(repo, 'gc', '--prune=now')
            if method == 'forklift':
                with self.stage('forklift_cleanup'):
                    self.fk(base/'store', 'cleanup', '--yes')
                with self.stage('forklift_gc'):
                    self.fk(base/'store', 'gc', '--yes')
        self.timed('maintenance', maintain)
        self.storage(method, base, repo, 'after_maintenance')
        self.git(repo, 'fsck', '--full')
        if method.endswith('lfs'):
            self.git(repo, 'lfs', 'fsck', '--objects', '--pointers', 'HEAD')
        if method == 'forklift':
            self.fk(base/'store', 'stop', 'main')
        else:
            self.stop(base/'live')
            self.run([self.a.pg_bin/'pg_checksums', '--check', '-D', base/'live'])

    def execute(self):
        metadata = {'argv': sys.argv, 'platform': platform.platform(), 'python': platform.python_version(),
            'hardware': self.run(['/usr/sbin/sysctl', '-n', 'machdep.cpu.brand_string']),
            'memory_bytes': self.run(['/usr/sbin/sysctl', '-n', 'hw.memsize']),
            'git': self.run(['git', '--version']), 'lfs': self.run(['git', 'lfs', 'version']),
            'postgres': self.run([self.a.pg_bin/'postgres', '--version']),
            'revision': self.git(REPO, 'rev-parse', 'HEAD'), 'binary_sha256': digest(self.a.bin.read_bytes()),
            'script_sha256': digest(Path(__file__).read_bytes()), 'root': str(self.root),
            'start_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            'cache': 'uncontrolled warm OS cache, serial operations, no purge', 'completed': False}
        try:
            for i, size in enumerate(self.a.sizes_mib):
                # Reverse order for second size to reduce fixed method-order bias.
                for method in (METHODS if i % 2 == 0 else list(reversed(METHODS))):
                    self.workflow(size, method)
            metadata['completed'] = True
        finally:
            errors = []
            for data in list(self.servers):
                try:
                    self.stop(data)
                except Exception as e:
                    errors.append(str(e))
            for store in self.stores:
                for name in ('main',):
                    try:
                        self.fk(store, 'stop', name)
                    except Exception as e:
                        errors.append(str(e))
                # A failed provision may leave the second branch running.
                listing = self.fk(store, 'list')
                self.log('cleanup.jsonl.gz', {'store': str(store), 'listing': listing})
                if 'second' in json.dumps(listing):
                    try:
                        self.fk(store, 'stop', 'second')
                    except Exception as e:
                        errors.append(str(e))
            live = list(self.root.rglob('postmaster.pid'))
            metadata.update(cleanup_errors=errors, remaining_pid_files=list(map(str, live)),
                            all_servers_stopped=not errors and not live)
            (self.out/'environment.json').write_text(json.dumps(metadata, indent=2)+'\n')
            if not errors and not live:
                shutil.rmtree(self.sockets)
            assert metadata['all_servers_stopped'], metadata
        # Cross-method equality of all expected application/schema/row/source states.
        states = [json.loads(s) for s in gzip.open(self.out/'states.jsonl.gz', 'rt')]
        grouped = {}
        for s in states:
            key = (s['size_mib'], s['phase'], s['sample'])
            if key in grouped:
                assert grouped[key] == s['state'], (key, s['method'])
            grouped[key] = s['state']
        (self.out/'SUCCESS').write_text('all operations and cross-method equality passed; all servers stopped\n')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--bin', type=Path, default=REPO/'target/release/forklift')
    p.add_argument('--pg-bin', type=Path, default=Path('/opt/homebrew/opt/postgresql@17/bin'))
    p.add_argument('--lfs-bin', type=Path, default=REPO/'benchmark-data/tools/git-lfs-3.7.1/git-lfs')
    p.add_argument('--sizes-mib', type=lambda s: [int(v) for v in s.split(',')], default=[8, 64])
    p.add_argument('--samples', type=int, default=3)
    a = p.parse_args()
    if not __debug__ or a.samples < 1 or any(s < 1 for s in a.sizes_mib):
        p.error('assertions must be enabled; positive samples and sizes required')
    Experiment(a).execute()


if __name__ == '__main__':
    main()
