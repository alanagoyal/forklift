#!/usr/bin/env python3
"""Generate auditable tables from benchmark_git.py receipts."""
import argparse
import collections
import gzip
import json
from pathlib import Path
import statistics


def read(root, name):
    return [json.loads(s) for s in gzip.open(root/name, 'rt')]


def spread(values):
    return f'{statistics.median(values):.3f} [{min(values):.3f}, {max(values):.3f}]'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('results', type=Path)
    a = p.parse_args()
    samples = read(a.results, 'samples.jsonl.gz')
    storage = read(a.results, 'storage.jsonl.gz')
    groups = collections.defaultdict(list)
    for s in samples:
        groups[(s['size_mib'], s['method'], s['phase'], s['operation'])].append(s)
    lines = ['# Generated measurements', '', 'Seconds: median [minimum, maximum]. Initial has n=1; change classes have n=3 in the full run.', '',
             '| MiB | Method | State | n | Capture | Restore | Provision |',
             '| ---: | --- | --- | ---: | ---: | ---: | ---: |']
    keys = sorted({k[:3] for k in groups})
    for size, method, phase in keys:
        entries = [groups[(size, method, phase, op)] for op in ('capture', 'restore', 'provision')]
        lines.append(f'| {size} | {method} | {phase} | {len(entries[0])} | '+
                     ' | '.join(spread([s['elapsed_s'] for s in entries_]) for entries_ in entries)+' |')
    lines += ['', '## Stage medians', '', 'Non-overlapping harness stages; medians need not sum to the median total.', '',
              '| MiB | Method | State | Operation | Stage | Seconds |', '| ---: | --- | --- | --- | --- | ---: |']
    for key, records in sorted(groups.items()):
        for stage in sorted({s for r in records for s in r['stages_s']}):
            lines.append('| '+' | '.join(map(str, key))+f' | {stage} | '+spread([r['stages_s'][stage] for r in records])+' |')
    lines += ['', '## Apparent storage', '', 'MiB of file lengths, not unique physical disk space. Git includes LFS; columns overlap.', '',
              '| MiB | Method | Point | Total | Git incl. LFS | Git objects | LFS objects | Snapshot copy | Live baseline | Forklift store |',
              '| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    for r in storage:
        if r['point'] not in ('before_maintenance', 'after_maintenance') and not (r['phase']=='schema' and r['sample']==2 and r['point'] in ('with_second', 'after_capture')):
            continue
        fields = ['total', 'git', 'git_objects', 'lfs_objects', 'snapshot_working_copy', 'live', 'forklift_store']
        lines.append(f"| {r['size_mib']} | {r['method']} | {r['point']} | "+' | '.join(f"{r[f]['apparent_bytes']/2**20:.2f}" for f in fields)+' |')
    lines += ['', '## Setup', '', 'Fresh repository, cluster initialization, deterministic data load and initial verification; compilation/tool download excluded.', '',
              '| MiB | Method | Seconds | Database bytes |', '| ---: | --- | ---: | ---: |']
    for r in read(a.results, 'setup.jsonl.gz'):
        lines.append(f"| {r['size_mib']} | {r['method']} | {r['elapsed_s']:.3f} | {r['database_bytes']} |")
    (a.results/'tables.md').write_text('\n'.join(lines)+'\n')
    summary = [{'size_mib': k[0], 'method': k[1], 'phase': k[2], 'operation': k[3], 'n': len(rs),
                'median_s': statistics.median(r['elapsed_s'] for r in rs),
                'min_s': min(r['elapsed_s'] for r in rs), 'max_s': max(r['elapsed_s'] for r in rs)}
               for k, rs in sorted(groups.items())]
    (a.results/'summary.json').write_text(json.dumps(summary, indent=2)+'\n')


if __name__ == '__main__':
    main()
