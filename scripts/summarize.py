#!/usr/bin/env python3
"""Summarize only completed runs. Small samples do not support tail-latency claims."""
import csv
import statistics
import sys
from pathlib import Path

root = Path(sys.argv[1])
if not (root / 'SUCCESS').is_file():
    raise SystemExit('refusing to summarize a run without SUCCESS')
with (root / 'metrics.tsv').open() as stream:
    rows = list(csv.DictReader(stream, delimiter='\t'))
print('| Method | Trials | Median branch → verified query | Min–max |')
print('| --- | ---: | ---: | ---: |')
for method in ('full-copy', 'apfs-clone', 'dump-restore'):
    values = [float(row['value']) for row in rows
              if row['method'] == method and row['stage'] == 'branch_to_verified_query']
    print(f'| {method} | {len(values)} | {statistics.median(values):.3f} s '
          f'| {min(values):.3f}–{max(values):.3f} s |')
