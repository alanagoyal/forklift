#!/usr/bin/env python3
"""Reduce nested timing spans without double counting child wall time."""
import collections
import gzip
import json
from pathlib import Path
import statistics
import sys

def phases(record):
    spans=sorted((s for s in record['spans'] if 'phase' in s),key=lambda s:(s['start_ms'],-s['elapsed_ms']))
    totals=collections.defaultdict(float);inclusive=collections.defaultdict(float);stack=[]
    for s in spans:
        start=s['start_ms'];end=start+s['elapsed_ms']
        while stack and start >= stack[-1][0]-0.000001: stack.pop()
        totals[s['phase']]+=s['elapsed_ms'];inclusive[s['phase']]+=s['elapsed_ms']
        if stack:
            assert end <= stack[-1][0]+0.001, 'crossing spans'
            totals[stack[-1][1]]-=s['elapsed_ms']
        stack.append((end,s['phase']))
    stop='native_stop' if record['backend']=='supabase' else 'pg_stop'
    start='native_start_readiness' if record['backend']=='supabase' else 'pg_start_readiness'
    stops=[s for s in spans if s['phase']==stop]
    starts=[s for s in spans if s['phase']==start]
    # Conservative service maintenance window, not sampled client-observed outage.
    window=None
    if stops:
        t=min(s['start_ms'] for s in stops)
        ends=[s['start_ms']+s['elapsed_ms'] for s in starts if s['start_ms']>=t]
        if ends: window=(max(ends)-t)/1000
    return dict(totals),dict(inclusive),window

def main():
    rows=[]
    for name in sys.argv[1:]:
        path=Path(name)
        text=gzip.open(path,'rt').read() if path.suffix=='.gz' else path.read_text()
        rows.extend(json.loads(line) for line in text.splitlines())
    groups=collections.defaultdict(list)
    for row in rows:
        exclusive,inclusive,window=phases(row)
        row['exclusive_ms']=exclusive;row['inclusive_ms']=inclusive;row['service_window_s']=window
        groups[(row['backend'],row['fixture']['requested_payload_mib'],row['operation'],row['restore_cache'])].append(row)
    summary=[]
    for key,rs in groups.items():
        times=[r['elapsed_s'] for r in rs]
        summary.append({'backend':key[0],'mib':key[1],'operation':key[2],'restore_cache':key[3],
                        'n':len(rs),'median_s':statistics.median(times),'min_s':min(times),'max_s':max(times),
                        'service_window_median_s':statistics.median(r['service_window_s'] for r in rs) if all(r['service_window_s'] is not None for r in rs) else None,
                        'exclusive_median_ms':{k:statistics.median(r['exclusive_ms'].get(k,0) for r in rs) for k in sorted(set().union(*(r['exclusive_ms'] for r in rs)))},
                        'inclusive_median_ms':{k:statistics.median(r['inclusive_ms'].get(k,0) for r in rs) for k in sorted(set().union(*(r['inclusive_ms'] for r in rs)))}})
    print(json.dumps(summary,indent=2))
if __name__=='__main__': main()
