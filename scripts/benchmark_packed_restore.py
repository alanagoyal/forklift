#!/usr/bin/env python3
"""Separate post-maintenance restore supplement; requires a successful primary run."""
import argparse
import gzip
import json
import shutil
import sys
import time
from pathlib import Path
from benchmark_backends import Backends, GIT
from benchmark_git import Experiment, REPO, digest


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--fixtures', type=Path, required=True)
    p.add_argument('--primary-results', type=Path, required=True)
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--bin', type=Path, default=REPO/'target/release/forklift')
    p.add_argument('--pg-bin', type=Path, default=Path('/opt/homebrew/opt/postgresql@17/bin'))
    a = p.parse_args()
    assert __debug__
    primary = json.loads((a.primary_results/'environment.json').read_text())
    assert (a.primary_results/'SUCCESS').exists() and primary['all_servers_stopped']
    assert a.fixtures.resolve() == Path(primary['root']).resolve()
    assert digest(a.bin.read_bytes()) == primary['binary_sha256']
    states = [json.loads(line) for line in gzip.open(a.primary_results/'states.jsonl.gz', 'rt')]
    expected = {(r['size_mib'], r['method'], r['sample']): r['state'] for r in states if r['phase']=='rows'}
    a.lfs_bin = GIT.parent/'unused'
    e = Backends(a)
    metadata = {'argv': sys.argv, 'primary_environment': primary,
                'script_sha256': digest(Path(__file__).read_bytes()),
                'start_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'completed': False}
    try:
        for i, size in enumerate(sorted({k[0] for k in expected})):
            for backend in (['chunked', 'git'] if i%2==0 else ['git', 'chunked']):
                e.backend = backend
                base = a.fixtures.resolve()/backend/f'{size}-forklift'
                store, repo = base/'store', base/'repo'
                e.context = {'size_mib': size, 'method': backend, 'phase': 'post_maintenance_rows', 'operation': 'setup'}
                assert not list((store/'cache').glob('*/data/PG_VERSION')), 'primary maintenance must remove caches'
                if backend=='git':
                    assert list((store/'git-store/repo/objects/pack').glob('*.pack'))
                e.fk(store, 'start', 'main')
                for sample in sorted(k[2] for k in expected if k[:2]==(size,backend)):
                    e.context['sample'] = sample
                    name = f'rows-{sample}'
                    commits = e.git(repo, 'log', '--all', '--format=%H', '--fixed-strings', f'--grep={name}').splitlines()
                    assert len(commits)==1
                    for operation in ['restore', 'restore_warm']:
                        e.context['operation'] = 'poison'
                        e.sql(e.connection('forklift', base), "UPDATE benchmark_private.payload SET value='post-pack-poison' WHERE id=1")
                        (repo/'src/file-0.txt').write_text('post-pack-poison')
                        def restore():
                            with e.stage('checkout_source'):
                                e.git(repo, 'restore', '--source', commits[0], '--worktree', 'src')
                            with e.stage('reset'):
                                result = e.fk(store, 'reset', 'main', '--to', name, '--yes')
                                wanted = 'cache_build_verify_publish' if operation=='restore' else 'cache_hit_verify'
                                assert wanted in [v['stage'] for v in result['events']]
                            with e.stage('start'):
                                e.fk(store, 'start', 'main')
                            with e.stage('verify'):
                                assert e.verify(repo, e.connection('forklift', base)) == expected[size,backend,sample]
                        Experiment.timed(e, operation, restore)
                e.fk(store, 'stop', 'main')
                e.fk(store, 'cleanup', '--yes')
                catalog = json.loads((store/'catalog.json').read_text())
                data = store/'generations'/catalog['branches']['main']['head']['id']/'data'
                e.run([a.pg_bin/'pg_checksums', '--check', '-D', data])
                if backend=='git':
                    e.run([GIT, '--git-dir', store/'git-store/repo', 'fsck', '--full'])
                print(f'completed post-maintenance {size} MiB {backend}', flush=True)
        metadata['completed'] = True
    finally:
        errors = []
        for store in e.stores:
            try:
                for branch in e.fk(store, 'list')['branches']:
                    if branch['state']=='running': e.fk(store, 'stop', branch['name'])
            except Exception as error:
                errors.append(str(error))
        live = list(a.fixtures.rglob('postmaster.pid'))
        metadata.update(cleanup_errors=errors, remaining_pid_files=list(map(str,live)),
                        all_servers_stopped=not errors and not live,
                        end_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
        (e.out/'environment.json').write_text(json.dumps(metadata,indent=2)+'\n')
        if not errors and not live: shutil.rmtree(e.sockets)
        assert metadata['all_servers_stopped'], metadata
    samples = [json.loads(line) for line in gzip.open(e.out/'samples.jsonl.gz','rt')]
    assert len(samples)==2*len(expected)
    (e.out/'SUCCESS').write_text('post-maintenance first and warm restores verified; all servers stopped\n')


if __name__=='__main__': main()
