#!/usr/bin/env python3
"""Generate tables and validate complete integrated-backend benchmark receipts."""
import collections, gzip, hashlib, json, statistics, sys
from pathlib import Path

root=Path(sys.argv[1])
def read(name): return [json.loads(l) for l in gzip.open(root/name,'rt')]
def spread(rs):
    vals=[r['elapsed_s'] for r in rs]
    return f'{statistics.median(vals):.3f} [{min(vals):.3f}–{max(vals):.3f}]'
samples=read('samples.jsonl.gz'); commands=read('commands.jsonl.gz'); storage=read('backend-storage.jsonl.gz')
groups=collections.defaultdict(list)
for r in samples: groups[(r['size_mib'],r['method'],r['phase'],r['operation'])].append(r)
lines=['# Integrated backend measurements','','Seconds: median [minimum–maximum]. Initial n=1; each change class n=3 in the full run. All operation times include verification.','','| Payload MiB | Backend | State | n | Capture | First restore | Warm restore | Independent provision |','| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: |']
for size,method,phase in sorted({k[:3] for k in groups}):
    entries=[groups[(size,method,phase,op)] for op in ['capture','restore','restore_warm','provision']]
    lines.append(f'| {size} | {method} | {phase} | {len(entries[0])} | '+' | '.join(map(spread,entries))+' |')
lines+=['','## Harness stages','','Non-overlapping wall-clock stage medians/ranges. Medians do not necessarily sum to the median total.','','| MiB | Backend | State | Operation | Stage | Seconds |','| ---: | --- | --- | --- | --- | ---: |']
for key,rs in sorted(groups.items()):
    for stage in sorted({s for r in rs for s in r['stages_s']}):
        vals=[{'elapsed_s':r['stages_s'][stage]} for r in rs]
        lines.append('| '+' | '.join(map(str,key))+f' | {stage} | {spread(vals)} |')
lines+=['','## Storage accounting','','MiB. Object history includes database snapshot objects/metadata plus application-source Git objects. `st_blocks*512` is allocated accounting, **not unique APFS physical usage**. Total includes old generations/caches until explicit maintenance.','','| MiB | Backend | Point | Object history apparent | Total apparent | Total allocated accounting |','| ---: | --- | --- | ---: | ---: | ---: |']
for r in storage:
    if r['point'] not in ('before_maintenance','after_maintenance') and not (r['point']=='with_second' and r['phase']=='schema' and r['sample']==2): continue
    history=r['database_history']['apparent_bytes']+r['source_history']['apparent_bytes']
    lines.append(f"| {r['size_mib']} | {r['method']} | {r['point']} | {history/2**20:.2f} | {r['total']['apparent_bytes']/2**20:.2f} | {r['total']['allocated_not_unique_bytes']/2**20:.2f} |")
(root/'tables.md').write_text('\n'.join(lines)+'\n')
summary=[dict(size_mib=k[0],method=k[1],phase=k[2],operation=k[3],n=len(rs),median_s=statistics.median(r['elapsed_s'] for r in rs),min_s=min(r['elapsed_s'] for r in rs),max_s=max(r['elapsed_s'] for r in rs)) for k,rs in sorted(groups.items())]
(root/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
assert all(c['exit']==0 for c in commands)
assert all(sum(r['stages_s'].values())<=r['elapsed_s']+0.001 for r in samples)
traces=[]
for c in commands:
    for line in c['stderr'].splitlines():
        if line.startswith('{'):
            try: v=json.loads(line)
            except ValueError: continue
            if v.get('forklift_timing'): traces.append(v)
assert traces and not any(t['truncated'] for t in traces)
events=read('forklift-events.jsonl.gz')
cache=collections.Counter((e['backend'],r['stage']) for e in events for r in e['events'] if r['stage'] in ('cache_hit_verify','cache_build_verify_publish'))
states=read('states.jsonl.gz'); observed={}
for r in states:
    key=(r['size_mib'],r['phase'],r['sample'])
    if key in observed: assert observed[key]==r['state']
    observed[key]=r['state']
env=json.loads((root/'environment.json').read_text()); assert env['completed'] and env['all_servers_stopped']
validation={'commands':len(commands),'all_commands_zero':True,'samples':len(samples),'operations':dict(collections.Counter(r['operation'] for r in samples)),
    'untruncated_traces':len(traces),'cache_events':{str(k):v for k,v in cache.items()},'state_records':len(states),'cross_backend_states_equal':True,
    'nonoverlapping_harness_stages':True,'all_servers_stopped':True,'receipt_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in root.glob('*.gz')}}
(root/'validation.json').write_text(json.dumps(validation,indent=2)+'\n')
print(json.dumps(validation,indent=2))
