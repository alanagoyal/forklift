#!/usr/bin/env python3
"""Validate and summarize the separate packed-history restore supplement."""
import collections
import gzip
import hashlib
import json
import statistics
import sys
from pathlib import Path

root = Path(sys.argv[1])
def read(name): return [json.loads(line) for line in gzip.open(root/name,'rt')]
samples = read('samples.jsonl.gz')
commands = read('commands.jsonl.gz')
env = json.loads((root/'environment.json').read_text())
assert env['completed'] and env['all_servers_stopped']
assert all(c['exit']==0 for c in commands)
groups = collections.defaultdict(list)
for s in samples:
    assert sum(s['stages_s'].values()) <= s['elapsed_s']+0.001
    groups[s['size_mib'],s['method'],s['operation']].append(s)
sizes = sorted({k[0] for k in groups})
assert set(groups)=={(size,backend,op) for size in sizes for backend in ['chunked','git'] for op in ['restore','restore_warm']}
assert all(sorted(s['sample'] for s in rs)==[0,1,2] for rs in groups.values())
summary = [dict(size_mib=k[0],method=k[1],operation=k[2],n=len(rs),
                median_s=statistics.median(r['elapsed_s'] for r in rs),
                min_s=min(r['elapsed_s'] for r in rs),max_s=max(r['elapsed_s'] for r in rs),
                stage_medians_s={stage:statistics.median(r['stages_s'][stage] for r in rs) for stage in rs[0]['stages_s']})
           for k,rs in sorted(groups.items())]
events = read('forklift-events.jsonl.gz')
cache = collections.Counter((e['backend'],v['stage']) for e in events for v in e['events'] if v['stage'] in ['cache_build_verify_publish','cache_hit_verify'])
assert all(cache[backend,stage]==len(sizes)*3 for backend in ['chunked','git'] for stage in ['cache_build_verify_publish','cache_hit_verify'])
verification = read('verification.jsonl.gz')
assert len(verification)==len(samples)
assert all(set(v['durability'].values())=={'on'} for v in verification)
validation = dict(commands=len(commands),samples=len(samples),all_commands_zero=True,all_servers_stopped=True,
                  durability_checks=len(verification),cache_events={str(k):v for k,v in cache.items()},
                  receipt_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in root.glob('*.gz')})
(root/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
(root/'validation.json').write_text(json.dumps(validation,indent=2)+'\n')
lines = ['# Post-maintenance restores','','Seconds: median [minimum–maximum], three historical row-edit states per backend/size. Separate from primary samples.','','| MiB | Backend | Operation | Seconds |','| ---: | --- | --- | ---: |']
for r in summary:
    lines.append(f"| {r['size_mib']} | {r['method']} | {r['operation']} | {r['median_s']:.3f} [{r['min_s']:.3f}–{r['max_s']:.3f}] |")
(root/'tables.md').write_text('\n'.join(lines)+'\n')
print(json.dumps(validation,indent=2))
