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
sizes=sorted({r['size_mib'] for r in states})
sample_count=int(env['argv'][env['argv'].index('--samples')+1]) if '--samples' in env['argv'] else 3
expected_states={(size,backend,phase,sample) for size in sizes for backend in ['chunked','git'] for phase in ['initial','unchanged','rows','schema'] for sample in range(1 if phase=='initial' else sample_count)}
assert {(r['size_mib'],r['method'],r['phase'],r['sample']) for r in states}==expected_states
for operation in ['capture','restore','restore_warm','provision']:
    selected=[r for r in samples if r['operation']==operation]
    assert len(selected)==len(expected_states)
    assert {(r['size_mib'],r['method'],r['phase'],r['sample']) for r in selected}==expected_states
verification=read('verification.jsonl.gz')
assert all(set(r['durability'].values())=={'on'} for r in verification)
# Inclusive phase durations: merge nested spans of the same name so an outer
# object_materialize span does not double-count its identically named child.
# Different phase names still overlap and must not be added together.
internal=collections.defaultdict(list)
for c in commands:
    if c.get('size_mib')!=max(sizes) or c.get('phase')!='rows' or c['stage'] not in ['checkpoint','reset','branch']: continue
    intervals=collections.defaultdict(list)
    for line in c['stderr'].splitlines():
        if not line.startswith('{'): continue
        t=json.loads(line)
        if not t.get('forklift_timing'): continue
        for span in t['spans']:
            if 'phase' in span: intervals[span['phase']].append((span['start_ms'],span['start_ms']+span['elapsed_ms']))
    for phase,spans in intervals.items():
        merged=[]
        for start,end in sorted(spans):
            if merged and start<=merged[-1][1]: merged[-1][1]=max(merged[-1][1],end)
            else: merged.append([start,end])
        internal[c['method'],c['operation'],phase].append(sum(end-start for start,end in merged)/1000)
internal_rows=[dict(backend=k[0],operation=k[1],phase=k[2],n=len(v),median_s=statistics.median(v),min_s=min(v),max_s=max(v)) for k,v in sorted(internal.items())]
(root/'internal-spans-largest-rows.json').write_text(json.dumps(internal_rows,indent=2)+'\n')
validation={'commands':len(commands),'all_commands_zero':True,'samples':len(samples),'operations':dict(collections.Counter(r['operation'] for r in samples)),
    'untruncated_traces':len(traces),'cache_events':{str(k):v for k,v in cache.items()},'state_records':len(states),'cross_backend_states_equal':True,
    'nonoverlapping_harness_stages':True,'durability_checks':len(verification),'all_servers_stopped':True,'receipt_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in root.glob('*.gz')}}
(root/'validation.json').write_text(json.dumps(validation,indent=2)+'\n')
print(json.dumps(validation,indent=2))
