#!/usr/bin/env python3
"""Serial, private checkpoint baseline. No hosted services or credential logs.
See docs/checkpoint-costs.md for timing and cache semantics.
"""
import argparse
import hashlib
import gzip
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import urllib.parse

REPO = Path(__file__).resolve().parents[1]

def main():
    p = argparse.ArgumentParser(description=__doc__)
    if not __debug__:
        raise SystemExit('Run without python -O; fixture verification uses assertions.')
    p.add_argument('--build-revision', help='revision used to build --bin (especially a saved baseline executable)')
    p.add_argument('--backend', choices=['postgres', 'supabase'], required=True)
    p.add_argument('--bin', type=Path, default=REPO/'target/release/forklift')
    p.add_argument('--verify-bin', type=Path, help='also restore the saved capture with another build, outside timing')
    p.add_argument('--pg-bin', type=Path, default=Path('/opt/homebrew/opt/postgresql@17/bin'))
    p.add_argument('--supabase-bin', type=Path)
    p.add_argument('--supabase-cache', type=Path)
    p.add_argument('--booklist', type=Path)
    p.add_argument('--sizes-mib', default='1,32,128')
    p.add_argument('--samples', type=int, default=3)
    p.add_argument('--copy-mode', choices=['apfs','full'], default='apfs')
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    try:
        sizes=[int(v) for v in a.sizes_mib.split(',')]
        if not sizes or any(v < 1 for v in sizes) or len(set(sizes)) != len(sizes) or a.samples < 2:
            raise ValueError
    except ValueError:
        p.error('provide distinct positive sizes and at least two samples')
    if a.backend == 'supabase' and not (a.supabase_bin and a.supabase_cache):
        p.error('native Supabase requires --supabase-bin and --supabase-cache')
    a.bin = a.bin.resolve(); a.output = a.output.resolve()
    a.output.mkdir(parents=True, exist_ok=False)
    (REPO/'benchmark-data').mkdir(exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix='checkpoint-', dir=REPO/'benchmark-data'))
    root.chmod(0o700)
    env = {k:v for k,v in os.environ.items() if k in ['PATH','HOME','TMPDIR','LANG']}
    if a.supabase_bin: env['FORKLIFT_SUPABASE_BIN'] = str(a.supabase_bin.resolve())
    records = []; apps = []; stores = []; states = []
    def run(argv, cwd=None, extra=None, expected=0):
        start = time.perf_counter()
        r = subprocess.run(list(map(str,argv)), cwd=cwd, env=dict(env,**(extra or {})), capture_output=True, text=True, timeout=1200)
        if r.returncode != expected:
            # Never echo arbitrary argv/stdout/stderr (may contain local credentials).
            try: code=json.loads(r.stdout).get('error',{}).get('code','unknown')
            except (ValueError,AttributeError): code='non-json'
            raise RuntimeError(f'command failed with exit {r.returncode}, code {code}; output suppressed')
        spans=[]
        for line in r.stderr.splitlines():
            if line.startswith('{"forklift_timing":'):
                trace=json.loads(line); assert not trace['truncated']; spans=trace['spans']
        return r.stdout.strip(), time.perf_counter()-start, spans
    def project(app,*args,**kw):
        return run([a.bin,'--json','--pg-bin',a.pg_bin,'--copy-mode',a.copy_mode,'project','--root',app,*args],**kw)
    def db(store,*args,**kw):
        return run([a.bin,'--json','--pg-bin',a.pg_bin,'--home',store,'--copy-mode',a.copy_mode,*args],**kw)
    def result(output): return json.loads(output[0])['result']
    def sql(uri,statement):
        return run([a.pg_bin/'psql',uri,'-XAt','-v','ON_ERROR_STOP=1','-c',statement])[0]
    def usage(path):
        files=[x for x in path.rglob('*') if x.is_file() and not x.is_symlink()]
        return {'files':len(files),'apparent_bytes':sum(x.stat().st_size for x in files)}
    def connection(app):
        code='import os,json; print(json.dumps({k:v for k,v in os.environ.items() if k in ["DATABASE_URL","NEXT_PUBLIC_SUPABASE_URL","NEXT_PUBLIC_SUPABASE_ANON_KEY","SUPABASE_SERVICE_ROLE_KEY"]}))'
        return json.loads(project(app,'run','--',sys.executable,'-c',code)[0])
    def api(e,path,data=None):
        key=e['SUPABASE_SERVICE_ROLE_KEY']
        payload=json.dumps(data).encode() if isinstance(data,dict) else data
        req=urllib.request.Request(e['NEXT_PUBLIC_SUPABASE_URL']+path,data=payload,headers={'apikey':key,'Authorization':'Bearer '+key,'Content-Type':'application/json' if isinstance(data,dict) else 'application/octet-stream','x-upsert':'true'})
        with urllib.request.urlopen(req,timeout=60) as response: return response.read()
    def booklist(app,e):
        if not a.booklist: return None
        # The full checked-in migration is applied by native setup; no edits to it.
        lines=(REPO/'examples/booklist/schema.sql').read_text().splitlines()
        sql(e['DATABASE_URL'],'\n'.join(line for table in ['books','people','recommendations'] for line in lines if line.startswith('INSERT INTO '+table+' ')))
        dump=root/(app.name+'-dump');dump.mkdir()
        hashes={}
        for name in ['scripts/dump-data.ts','utils/supabase/server.ts','tsconfig.json','package.json']:
            dest=dump/name;dest.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(a.booklist/name,dest)
            hashes[name]=hashlib.sha256(dest.read_bytes()).hexdigest()
        (dump/'node_modules').symlink_to((a.booklist/'node_modules').resolve(),target_is_directory=True)
        def verify():
            run([dump/'node_modules/.bin/tsx','scripts/dump-data.ts'],cwd=dump,extra={k:e[k] for k in ['NEXT_PUBLIC_SUPABASE_URL','NEXT_PUBLIC_SUPABASE_ANON_KEY']})
            assert len(json.loads((dump/'public/data/books-essential.json').read_text()))==2
            assert len(json.loads((dump/'public/data/recommenders.json').read_text()))==2
        verify()
        metadata['booklist']={'files_sha256':hashes,'migration_sha256':hashlib.sha256((a.booklist/'supabase/migrations/20250605153046_initial.sql').read_bytes()).hexdigest(),'private_config_major':17,'synthetic_books':2}
        return verify
    metadata={'backend':a.backend,'copy_mode':a.copy_mode,'sizes_mib':a.sizes_mib,'samples':a.samples,
              'hardware':run(['/usr/sbin/sysctl','-n','machdep.cpu.brand_string'])[0],
              'memory_bytes':int(run(['/usr/sbin/sysctl','-n','hw.memsize'])[0]),
              'platform':platform.platform(),'machine':platform.machine(),'python':platform.python_version(),
              'postgres':run([a.pg_bin/'postgres','--version'])[0],
              'binary_sha256':hashlib.sha256(a.bin.read_bytes()).hexdigest(),
              'build_revision':a.build_revision or run(['git','rev-parse','HEAD'],cwd=REPO)[0],
              'rustc':run([Path.home()/'.cargo/bin/rustc','--version'])[0],
              'os_cache':'uncontrolled; no purge; first operation is not cold OS cache',
              'durability_verified':'fsync=on, full_page_writes=on, synchronous_commit=on, data_checksums=on',
              'app_unavailability':'managed application deliberately stopped for entire capture/restore; no application server launched',
              'started_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())}
    if a.supabase_bin: metadata['supabase_cli']=run([a.supabase_bin,'--version'])[0]
    try:
        for size in sizes:
            app=root/f'app-{size}';app.mkdir();apps.append(app)
            (app/'src').mkdir()
            for i in range(64): (app/'src'/f'file-{i}.txt').write_text(hashlib.sha256(str(i).encode()).hexdigest()*256)
            sources=['src']
            if a.backend=='supabase':
                assert a.supabase_bin and a.supabase_cache
                (app/'supabase').mkdir()
                (app/'supabase/config.toml').write_text('project_id="checkpoint-cost-fixture"\n[db]\nmajor_version=17\n')
                sources.append('supabase/config.toml')
                if a.booklist:
                    (app/'supabase/migrations').mkdir()
                    shutil.copyfile(a.booklist/'supabase/migrations/20250605153046_initial.sql',app/'supabase/migrations/20250605153046_initial.sql')
                    sources.append('supabase/migrations')
            (app/'.forklift.json').write_text(json.dumps({'format':1,'backend':a.backend,'source':sources,'commands':{}}))
            run(['git','init','--quiet',app])
            setup_start=time.perf_counter()
            if a.backend=='postgres':
                store=root/f'store-{size}';stores.append(store)
                db(store,'init','seed');db(store,'start','seed');db(store,'checkpoint','seed','--name','seed-capture');db(store,'stop','seed')
                project(app,'configure','--store',store,'--checkpoint','seed-capture');project(app,'setup')
            else:
                project(app,'setup',extra={'FORKLIFT_SUPABASE_BIN':str(root/'missing')},expected=1)
                admin=app/'.git';st=admin/'forklift-workspaces'/json.loads((admin/'forklift-id.json').read_text())
                states.append(st);(st/'home').mkdir(mode=0o700,exist_ok=True)
                run(['/bin/cp','-cR',a.supabase_cache,st/'home/cache'])
                project(app,'setup')
            setup_s=time.perf_counter()-setup_start
            e=connection(app);uri=e['DATABASE_URL']
            assert urllib.parse.urlsplit(uri).hostname in ['127.0.0.1','localhost',None]
            assert sql(uri,"SELECT current_setting('fsync')||','||current_setting('full_page_writes')||','||current_setting('synchronous_commit')||','||current_setting('data_checksums')")=='on,on,on,on'
            verify_booklist=booklist(app,e) if a.backend=='supabase' else None
            # Deterministic, mostly incompressible text: 512 rows ~= 1 MiB before row/index overhead.
            sql(uri,f"CREATE SCHEMA benchmark_private; CREATE TABLE benchmark_private.payload(id integer PRIMARY KEY, value text); INSERT INTO benchmark_private.payload SELECT i, (SELECT string_agg(md5(i::text||':'||j::text),'') FROM generate_series(1,64) j) FROM generate_series(1,{size*512}) i;")
            if a.backend=='supabase':
                api(e,'/storage/v1/bucket',{'id':'benchmark','name':'benchmark','public':False})
                for i in range(size):
                    # Distinct deterministic bytes, split into 1 MiB objects.
                    data=b''.join(hashlib.sha256(f'{i}:{j}'.encode()).digest() for j in range(32768))
                    api(e,f'/storage/v1/object/benchmark/{i}.bin',data)
            fixture={'requested_payload_mib':size,'source':usage(app/'src'),'rows':size*512,
                     'database_bytes':int(sql(uri,"SELECT pg_database_size(current_database())")),
                     'setup_s':setup_s,'setup_cache':'fresh fixture; cloned native artifact cache' if a.backend=='supabase' else 'fresh store; seed cache built during setup'}
            if a.backend=='supabase':
                fixture['stacks']=usage(st/'home/stacks');fixture['storage']=usage(st/'runtime/supabase/.temp/stack-uploads')
                fixture['runtime_version']=sql(uri,'SELECT version()')
                fixture['runtime_artifacts']=usage(st/'home/cache/stack/slim-services')
            else:
                fixture['pgdata']=usage(Path(sql(uri,'SHOW data_directory')))
            print(f'{a.backend} {size} MiB fixture ready',flush=True)
            for sample in range(a.samples):
                for op in ['capture','restore']:
                    if op=='restore':
                        (app/'src/file-0.txt').write_text('changed')
                        sql(uri,"UPDATE benchmark_private.payload SET value='changed' WHERE id=1")
                        if a.backend=='supabase': api(e,'/storage/v1/object/benchmark/0.bin',b'changed')
                    name=f'sample_{sample}' if op=='capture' else 'sample_0'
                    out,elapsed,spans=project(app,op,name,extra={'FORKLIFT_TIMING':'1'})
                    assert spans, 'binary must contain timing instrumentation'
                    for span in spans:
                        assert set(span) in ({'phase','start_ms','elapsed_ms'},{'object_stats'})
                        if 'phase' in span:
                            assert all(c.islower() or c=='_' for c in span['phase'])
                            assert span['start_ms'] >= 0 and span['elapsed_ms'] >= 0
                    record={'backend':a.backend,'operation':op,'sample':sample,'fixture':fixture,
                            'capture_state':('first loaded capture' if sample==0 else 'repeated capture') if op=='capture' else 'not applicable',
                            'restore_cache':('miss' if sample==0 else 'hit') if a.backend=='postgres' and op=='restore' else 'not applicable',
                            'elapsed_s':elapsed,'spans':spans}
                    records.append(record)
                    with gzip.open(a.output/'samples.jsonl.gz','at') as f: f.write(json.dumps(record)+'\n')
                    print(f'{op} sample {sample}: {elapsed:.3f}s',flush=True)
                    e=connection(app);uri=e['DATABASE_URL']
                    assert sql(uri,'SELECT count(*) FROM benchmark_private.payload')==str(size*512)
                    assert sql(uri,"SELECT value <> 'changed' FROM benchmark_private.payload WHERE id=1")=='t'
                    assert (app/'src/file-0.txt').stat().st_size==16384
                    if a.backend=='supabase':
                        expected=b''.join(hashlib.sha256(f'0:{j}'.encode()).digest() for j in range(32768))
                        assert api(e,'/storage/v1/object/benchmark/0.bin')==expected
                if verify_booklist: verify_booklist()
            if a.verify_bin:
                measured_bin=a.bin
                try:
                    a.bin=a.verify_bin.resolve()
                    project(app,'restore','sample_0')
                    e=connection(app);uri=e['DATABASE_URL']
                    assert sql(uri,'SELECT count(*) FROM benchmark_private.payload')==str(size*512)
                    assert sql(uri,"SELECT value <> 'changed' FROM benchmark_private.payload WHERE id=1")=='t'
                    assert (app/'src/file-0.txt').stat().st_size==16384
                    if a.backend=='supabase':
                        assert api(e,'/storage/v1/object/benchmark/0.bin')==b''.join(hashlib.sha256(f'0:{j}'.encode()).digest() for j in range(32768))
                    if verify_booklist: verify_booklist()
                    metadata['cross_build_restore_sha256']=hashlib.sha256(a.bin.read_bytes()).hexdigest()
                finally: a.bin=measured_bin
            project(app,'stop')
        metadata['completed']=True
    finally:
        errors=[]
        for app in apps:
            try: project(app,'stop')
            except Exception: errors.append(app.name)
        for store in stores:
            try: db(store,'stop','seed')
            except Exception: errors.append(store.name)
        live=list(root.rglob('postmaster.pid'))+[x for st in states for x in (st/'home/stacks').glob('*/owner.json')]
        metadata['all_servers_stopped']=not errors and not live
        metadata['cleanup_errors']=errors
        metadata['fixture_root']=str(root)
        (a.output/'environment.json').write_text(json.dumps(metadata,indent=2)+'\n')
        assert metadata['all_servers_stopped'], 'fixture cleanup incomplete'

if __name__=='__main__': main()
