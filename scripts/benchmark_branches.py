#!/usr/bin/env python3
"""Measured M4 workload: 1/5/20/100 experiment branches, 0/1/3 active.
Run a release binary on an otherwise quiet Mac; never disables PG durability.
RSS is summed process RSS (shared pages may be counted repeatedly), not unique RAM.
"""
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import statistics
import subprocess
import tempfile
import time

PG = Path(os.environ.get('PG_BIN', '/opt/homebrew/opt/postgresql@17/bin'))
BIN = Path(os.environ.get('FORKLIFT_BIN', 'target/release/forklift')).resolve()
ROWS = int(os.environ.get('FORKLIFT_ROWS', '100000'))
ROOT = Path(tempfile.mkdtemp(prefix='branches-', dir='benchmark-data')).resolve()
HOME = ROOT/'store'
records, samples, creations, activations, resets = [], [], [], [], []
summary = {'root': str(ROOT), 'rows': ROWS, 'platform': platform.platform(),
           'binary_sha256': hashlib.sha256(BIN.read_bytes()).hexdigest(),
           'postgres': subprocess.check_output([str(PG/'postgres'), '--version'], text=True).strip(),
           'cpu': subprocess.check_output(['sysctl','-n','machdep.cpu.brand_string'], text=True).strip(),
           'ram_bytes': int(subprocess.check_output(['sysctl','-n','hw.memsize'], text=True)),
           'notes': ['source is parked; stored experiment counts exclude the one source branch',
                     'filesystem cache is uncontrolled; these are repeated local runs, not cold-disk tests',
                     'all file syncs and PostgreSQL durability enabled',
                     'sum RSS counts shared pages repeatedly; st_blocks is not unique APFS disk usage',
                     'single run; not a power-loss test or throughput comparison to another product']}


def cli(*args, code=None):
    t = time.monotonic()
    p = subprocess.run([str(BIN), '--home', str(HOME), '--pg-bin', str(PG), '--json', *args],
                       text=True, capture_output=True, timeout=180)
    elapsed = time.monotonic()-t
    obj = json.loads(p.stdout)
    if code: assert not obj['ok'] and obj['error']['code'] == code, (args, obj)
    else: assert p.returncode == 0 and obj['ok'], (args, obj, p.stderr)
    result = obj.get('result', {})
    records.append({'args': args, 'elapsed_seconds': elapsed, 'events': result.get('events', []),
                    'active_clusters': result.get('active_clusters'), 'error': obj.get('error')})
    return result, elapsed


def connection(name): return cli('connection', name)[0]['connection']
def sql(conn, query):
    t = time.monotonic()
    out = subprocess.check_output([str(PG/'psql'), conn, '-XAt', '-v', 'ON_ERROR_STOP=1', '-c', query], text=True).strip()
    return out, time.monotonic()-t

def stats(values):
    v = sorted(values)
    r = {'n': len(v), 'min': min(v), 'median': statistics.median(v), 'max': max(v)}
    if len(v) >= 20: r['p95_nearest_rank'] = v[math.ceil(.95*len(v))-1]
    return r

def digest(conn):
    return sql(conn, "SELECT count(*) || ':' || md5(string_agg(md5(row_to_json(a)::text), '' ORDER BY id)) FROM accounts a")[0]

def resources():
    catalog = json.loads((HOME/'catalog.json').read_text())
    parents = {b['head']['process']['pid'] for b in catalog['branches'].values() if b['head']['process']}
    lines = subprocess.check_output(['ps','-axo','pid=,ppid=,rss=,%cpu='], text=True).splitlines()
    table = {int(x[0]): (int(x[1]),int(x[2]),float(x[3])) for line in lines if len(x:=line.split()) == 4}
    included = set(parents)
    while True:
        more = {pid for pid, (ppid,_,_) in table.items() if ppid in included}
        if more.issubset(included): break
        included |= more
    return {'parent_servers': len(parents), 'processes': len(included),
            'sum_rss_kib': sum(table[p][1] for p in included if p in table),
            'sum_ps_cpu_percent': sum(table[p][2] for p in included if p in table)}


def activate(name, label):
    _, start = cli('start', name)
    t = time.monotonic(); conn = connection(name)
    value, query = sql(conn, 'SELECT count(*) FROM accounts')
    assert value == str(ROWS)
    activations.append({'name': name, 'phase': label, 'start_seconds': start,
                        'connection_and_query_seconds': time.monotonic()-t, 'psql_first_query_seconds': query})
    return conn

try:
    print('seed and materialization baselines', flush=True)
    cli('init', 'source'); cli('start', 'source'); conn = connection('source')
    subprocess.run([str(PG/'psql'),conn,'-X','-v','ON_ERROR_STOP=1','-v',f'rows={ROWS}','-f','fixtures/seed.sql'], check=True,capture_output=True)
    summary['original_digest'] = digest(conn)
    summary['durability'] = sql(conn, "SELECT current_setting('fsync'),current_setting('full_page_writes'),current_setting('synchronous_commit'),current_setting('data_checksums'),current_setting('shared_buffers')")[0]
    assert summary['durability'].startswith('on|on|on|on|')
    summary['database_size_bytes'] = int(sql(conn,'SELECT pg_database_size(current_database())')[0])
    summary['checkpoint'], summary['checkpoint_seconds'] = cli('checkpoint','source','--name','seed')
    cli('stop','source')
    # Reuse the same named branch for direct/full baselines; parked head and retained
    # previous generations are counted, so experiment branch count stays exact.
    direct = []
    r, t = cli('branch','--from','seed','--name','b001','--no-cache'); direct.append(t)
    for _ in range(2): direct.append(cli('reset','b001','--to','seed','--yes','--no-cache')[1])
    summary['direct_materialization_operations_seconds'] = direct
    summary['cold_cache_reset'], summary['cold_cache_reset_seconds'] = cli('reset','b001','--to','seed','--yes')
    summary['full_cache_reset_seconds'] = [cli('reset','b001','--to','seed','--yes','--copy-mode','full')[1] for _ in range(3)]
    summary['warm_cache_reset_seconds'] = [cli('reset','b001','--to','seed','--yes')[1] for _ in range(3)]
    created = 1
    workload = ROOT/'read.sql'; workload.write_text('SELECT sum(balance) FROM accounts;\n')
    for count in (1,5,20,100):
        print(f'creating and measuring {count} stored experiment branches', flush=True)
        while created < count:
            created += 1; name = f'b{created:03d}'
            r,t = cli('branch','--from','seed','--name',name)
            creations.append({'name':name,'seconds':t,'events':r['events']})
            if created % 10 == 0: print(f'  created {created}', flush=True)
        for active in (0,1,3):
            if active > count: continue
            conns = []
            for i in range(1,active+1): conns.append(activate(f'b{i:03d}', f'{count}-stored-{active}-active'))
            if active == 3: cli('start','source',code='running_limit')
            time.sleep(.5)
            idle = [resources() for _ in range(3)]
            assert all(r['parent_servers'] == active for r in idle)
            clients = [subprocess.Popen([str(PG/'pgbench'),conn,'-n','-c','1','-j','1','-T','2','-f',str(workload)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for conn in conns]
            if clients: time.sleep(.5)
            busy = resources()
            outcomes=[]
            for client in clients:
                out,err=client.communicate(timeout=30); assert client.returncode==0,(out,err); outcomes.append(out)
            samples.append({'stored_experiments':count,'stored_total':count+1,'active':active,
                            'idle_samples':idle,'read_workload_sample':busy,'pgbench_output':outcomes})
            for i in range(1,active+1): cli('stop',f'b{i:03d}')
        summary[f'storage_at_{count}'] = cli('stats')[0]

    print('write and restart every one of the 100 branches', flush=True)
    for i in range(1,101):
        name=f'b{i:03d}'; conn=activate(name,'write-persistence')
        sql(conn,f"CREATE TABLE branch_marker(value integer); INSERT INTO branch_marker VALUES({i})")
        cli('stop',name)
        if i % 10 == 0: print(f'  wrote {i}',flush=True)
    for i in range(1,101):
        name=f'b{i:03d}'; conn=activate(name,'verify-persistence')
        assert sql(conn,'SELECT value FROM branch_marker')[0] == str(i)
        assert digest(conn) == summary['original_digest']
        cli('stop',name)
        if i % 10 == 0: print(f'  verified {i}',flush=True)
    summary['persisted_branch_markers_verified'] = 100
    print('schema, 1% and 10% divergence; repeated reset experiments',flush=True)
    conns=[activate(f'b{i:03d}','divergence') for i in (1,2,3)]
    sql(conns[0],'ALTER TABLE accounts ADD COLUMN experiment text; CREATE INDEX experiment_balance ON accounts(balance)')
    for conn,fraction in zip(conns[1:],(.01,.10)):
        sql(conn,f'UPDATE accounts SET balance=balance+7 WHERE id<={int(ROWS*fraction)}')
        assert digest(conn) != summary['original_digest']
    summary['storage_after_divergence']=cli('stats')[0]
    for i in (1,2,3):
        r,t=cli('reset',f'b{i:03d}','--to','seed','--yes'); resets.append({'phase':'divergence','seconds':t,'events':r['events']})
        conn=connection(f'b{i:03d}'); assert digest(conn)==summary['original_digest']
        assert sql(conn,"SELECT to_regclass('branch_marker') IS NULL")[0]=='t'
        assert sql(conn,"SELECT count(*) FROM information_schema.columns WHERE table_name='accounts' AND column_name='experiment'")[0]=='0'
    for _ in range(5):
        sql(connection('b001'),'UPDATE accounts SET balance=balance+1 WHERE id<=1000')
        r,t=cli('reset','b001','--to','seed','--yes'); resets.append({'phase':'repeated','seconds':t,'events':r['events']})
        assert digest(connection('b001'))==summary['original_digest']
    for i in (1,2,3): cli('stop',f'b{i:03d}')
    cli('start','source'); assert digest(connection('source'))==summary['original_digest']; cli('stop','source')
    summary['final_storage']=cli('stats')[0]
    assert cli('list')[0]['active_clusters']==0 and not list(HOME.rglob('postmaster.pid'))
    summary['warm_branch_seconds']=stats([r['seconds'] for r in creations])
    summary['activation_seconds']=stats([r['start_seconds'] for r in activations])
    summary['first_query_psql_seconds']=stats([r['psql_first_query_seconds'] for r in activations])
    summary['running_reset_seconds']=stats([r['seconds'] for r in resets])
    (ROOT/'SUCCESS').write_text('100 branches persisted independently; isolation/reset and capacity verified\n')
    print('PASS: '+str(ROOT),flush=True)
finally:
    for data in (HOME/'generations').glob('*/data'):
        pid=data/'postmaster.pid'
        if pid.exists() and pid.read_text().splitlines()[1]==str(data):
            subprocess.run([str(PG/'pg_ctl'),'-D',str(data),'-m','fast','-w','stop'],capture_output=True)
    for name,value in [('summary',summary),('commands',records),('resources',samples),('creations',creations),('activations',activations),('resets',resets)]:
        (ROOT/(name+'.json')).write_text(json.dumps(value,indent=2)+'\n')
