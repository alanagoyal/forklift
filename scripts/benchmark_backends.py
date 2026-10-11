#!/usr/bin/env python3
"""Compare integrated chunked and ordinary Git backends using the unchanged fixture harness.

Source coordination is external to Forklift here; test_project.py separately tests
selected unfinished source and journal recovery on each backend. No application
commits are required by Forklift; these harness-only commits enumerate source states.
"""
import argparse
import gzip
import json
import os
from pathlib import Path
import platform
import shutil
import sys
import time
from benchmark_git import Experiment, REPO, digest

def usage(path):
    files=[p for p in path.rglob("*") if p.is_file() and not p.is_symlink()] if path.exists() else []
    return {"files":len(files),"apparent_bytes":sum(p.stat().st_size for p in files),
            "allocated_not_unique_bytes":sum(p.stat().st_blocks*512 for p in files)}

class Backends(Experiment):
    def log(self, file, value):
        if hasattr(self, 'backend') and 'method' in value:
            value = value | {'method': self.backend}
        super().log(file, value)

    def fk(self, store, *args):
        result = super().fk(store, *args)
        if isinstance(result,dict) and result.get('events'):
            self.log('forklift-events.jsonl.gz',self.context | {'backend':self.backend,'args':list(map(str,args)),'events':result['events']})
        if args[0] == 'reset':
            cache=[e['stage'] for e in result['events'] if e['stage'].startswith('cache_')]
            assert any(e in cache for e in ('cache_build_verify_publish','cache_hit_verify')),cache
            if self.context['operation']=='restore_warm': assert 'cache_hit_verify' in cache,cache
        if args[0] == 'init':
            super().fk(store, 'config', '--storage-backend', self.backend)
        return result

    def verify(self, repo, uri):
        state = super().verify(repo,uri)
        self.log('verification.jsonl.gz',self.context | {'source':str(repo),'state':state,
            'durability':{'fsync':'on','synchronous_commit':'on','full_page_writes':'on','data_checksums':'on'}})
        return state

    def timed(self, operation, action):
        result = super().timed(operation, action)
        if operation == 'restore':
            # Same checkpoint is now materialized. Explicitly change rows/source again
            # before timing the second reset; this is a warm verified-cache restore.
            base = self.root/f"{self.context['size_mib']}-forklift"
            self.sql(self.connection('forklift', base), "UPDATE benchmark_private.payload SET value='warm-poison' WHERE id=1")
            (base/'repo/src/file-0.txt').write_text('warm-poison')
            super().timed('restore_warm', action)
        return result

    def storage(self, method, base, repo, point):
        super().storage(method, base, repo, point)
        self.log('backend-storage.jsonl.gz', self.context | {'point':point,
            'total':usage(base), 'database_history':usage(base/'store'/('git-store' if self.backend=='git' else 'objects')),
            'source_history':usage(repo/'.git/objects'),
            'database_git_objects':usage(base/'store/git-store/repo/objects'),
            'note':'apparent file lengths; st_blocks and APFS clone sharing do not give unique physical usage'})

    def execute(self):
        original = self.root
        metadata = {'argv':sys.argv,'platform':platform.platform(),'python':platform.python_version(),
            'hardware':self.run(['/usr/sbin/sysctl','-n','machdep.cpu.brand_string']),
            'memory_bytes':self.run(['/usr/sbin/sysctl','-n','hw.memsize']),
            'git':self.run(['/opt/homebrew/bin/git','--version']),
            'source_git':self.run(['git','--version']),
            'postgres':self.run([self.a.pg_bin/'postgres','--version']),
            'revision':self.git(REPO,'rev-parse','HEAD'),
            'source_diff_sha256':digest(self.run(['git','diff','HEAD','--','src','scripts/benchmark_backends.py','scripts/benchmark_git.py']).encode()),
            'executables':{str(p.resolve()):digest(p.read_bytes()) for p in [Path('/opt/homebrew/bin/git'),*[self.a.pg_bin/n for n in ['postgres','pg_ctl','initdb','pg_dump','pg_restore','pg_checksums']]]}, 'binary_sha256':digest(self.a.bin.read_bytes()),
            'script_sha256':digest(Path(__file__).read_bytes()),
            'base_harness_sha256':digest((REPO/'scripts/benchmark_git.py').read_bytes()),
            'root':str(original),'start_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),
            'cache':'serial local warm OS cache; first cache build versus verified cache hit; no purge',
            'completed':False}
        try:
            for i,size in enumerate(self.a.sizes_mib):
                for self.backend in (['chunked','git'] if i%2==0 else ['git','chunked']):
                    self.root = original/self.backend
                    self.workflow(size,'forklift')
                    store = self.root/f'{size}-forklift/store'
                    if self.backend=='git':
                        self.run(['/opt/homebrew/bin/git','--git-dir',store/'git-store/repo','fsck','--full'])
                    listing = self.fk(store,'list')
                    for branch in listing['branches']:
                        if branch['name']=='main':
                            # Derive only the owned current generation from catalog metadata.
                            catalog=json.loads((store/'catalog.json').read_text())
                            data=store/'generations'/catalog['branches']['main']['head']['id']/'data'
                            self.run([self.a.pg_bin/'pg_checksums','--check','-D',data])
            metadata['completed']=True
        finally:
            errors=[]
            for store in self.stores:
                try:
                    for branch in self.fk(store,'list')['branches']:
                        if branch['state']=='running': self.fk(store,'stop',branch['name'])
                except Exception as e: errors.append(str(e))
            live=list(original.rglob('postmaster.pid'))
            metadata.update(cleanup_errors=errors,remaining_pid_files=list(map(str,live)),all_servers_stopped=not errors and not live,
                end_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()))
            (self.out/'environment.json').write_text(json.dumps(metadata,indent=2)+'\n')
            if not errors and not live: shutil.rmtree(self.sockets)
            assert metadata['all_servers_stopped'],metadata
        states=[json.loads(line) for line in gzip.open(self.out/'states.jsonl.gz','rt')]
        expected_count=len(self.a.sizes_mib)*2*(1+3*self.a.samples)
        assert len(states)==expected_count
        assert {s['method'] for s in states}=={'chunked','git'}
        samples=[json.loads(line) for line in gzip.open(self.out/'samples.jsonl.gz','rt')]
        for operation in ['capture','restore','restore_warm','provision']:
            assert len([s for s in samples if s['operation']==operation])==expected_count
        grouped={}
        for line in gzip.open(self.out/'states.jsonl.gz','rt'):
            s=json.loads(line); key=(s['size_mib'],s['phase'],s['sample'])
            if key in grouped: assert grouped[key]==s['state'],key
            grouped[key]=s['state']
        (self.out/'SUCCESS').write_text('both integrated backends passed history, first/warm-cache restores, independent databases, fingerprints, checksums and stopped-server checks\n')

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True); p.add_argument('--output',type=Path,required=True)
    p.add_argument('--bin',type=Path,default=REPO/'target/release/forklift')
    p.add_argument('--pg-bin',type=Path,default=Path('/opt/homebrew/opt/postgresql@17/bin'))
    p.add_argument('--sizes-mib',type=lambda s:list(map(int,s.split(','))),default=[8,64,256])
    p.add_argument('--samples',type=int,default=3)
    a=p.parse_args(); a.lfs_bin=Path('/opt/homebrew/bin/unused')
    if not __debug__ or a.samples<1 or any(s<1 for s in a.sizes_mib): p.error('positive sizes/samples and assertions required')
    Backends(a).execute()

if __name__=='__main__': main()
