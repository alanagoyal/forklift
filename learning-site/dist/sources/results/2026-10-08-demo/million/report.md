# Forklift demo results

Status: **passed**. Rows: 1,000,000.

Measurements come from this run. Filesystem cache is uncontrolled. RSS can count shared pages repeatedly; file bytes and block counts are not unique APFS disk usage.

## Query plan experiment

Five EXPLAIN ANALYZE samples before and after creating the index; unchanged query result. Fixed order, no forced planner settings. This is a SQL index effect, not a Forklift storage speedup.

| Phase | Median PostgreSQL execution time |
| --- | ---: |
| Before index | 21.853 ms |
| After index | 0.068 ms |

Full plans, buffer counts and timings: [summary.json](summary.json).

## Selected lifecycle operations

| Operation | Seconds (whole CLI) |
| --- | ---: |
| checkpoint source | 7.153 |
| reset delete-rows | 1.919 |
| checkpoint add-index | 6.209 |
| export indexed | 2.433 |
| restore /Users/alanagoyal/Developer/forklift/benchmark-data/demo-m5-million/indexed.forklift | 10.194 |

## 20 parked experiments + one parked source

```text
running clusters: 0/3
checkpoint seed
  └─ add-index: parked
  └─ change-schema: parked
  └─ delete-rows: parked
  └─ experiment-04: parked
  └─ experiment-05: parked
  └─ experiment-06: parked
  └─ experiment-07: parked
  └─ experiment-08: parked
  └─ experiment-09: parked
  └─ experiment-10: parked
  └─ experiment-11: parked
  └─ experiment-12: parked
  └─ experiment-13: parked
  └─ experiment-14: parked
  └─ experiment-15: parked
  └─ experiment-16: parked
  └─ experiment-17: parked
  └─ experiment-18: parked
  └─ experiment-19: parked
  └─ experiment-20: parked
independent / restored branches
  └─ source: parked
saved seed ← source [ready]
```

Observed: 0 clusters, 0 PostgreSQL processes, 0.0 MiB summed RSS.
Store apparent bytes: 17,190,198,286; allocated block bytes (not unique): 17,192,185,856.

## Three active experiments: index, schema change, and 10% deletion

```text
running clusters: 3/3
checkpoint seed
  └─ add-index: running
  └─ change-schema: running
  └─ delete-rows: running
  └─ experiment-04: parked
  └─ experiment-05: parked
  └─ experiment-06: parked
  └─ experiment-07: parked
  └─ experiment-08: parked
  └─ experiment-09: parked
  └─ experiment-10: parked
  └─ experiment-11: parked
  └─ experiment-12: parked
  └─ experiment-13: parked
  └─ experiment-14: parked
  └─ experiment-15: parked
  └─ experiment-16: parked
  └─ experiment-17: parked
  └─ experiment-18: parked
  └─ experiment-19: parked
  └─ experiment-20: parked
independent / restored branches
  └─ source: parked
saved seed ← source [ready]
```

Observed: 3 clusters, 18 PostgreSQL processes, 94.5 MiB summed RSS.
Store apparent bytes: 17,198,970,938; allocated block bytes (not unique): 17,200,975,872.

## Export restored independently, including the saved index

```text
running clusters: 1/3
independent / restored branches
  └─ restored-index: running
```

Observed: 1 clusters, 6 PostgreSQL processes, 33.4 MiB summed RSS.
Store apparent bytes: 768,904,946; allocated block bytes (not unique): 785,375,232.

## Completed: original branches parked

```text
running clusters: 0/3
checkpoint seed
  └─ add-index: parked
  └─ change-schema: parked
  └─ delete-rows: parked
  └─ experiment-04: parked
  └─ experiment-05: parked
  └─ experiment-06: parked
  └─ experiment-07: parked
  └─ experiment-08: parked
  └─ experiment-09: parked
  └─ experiment-10: parked
  └─ experiment-11: parked
  └─ experiment-12: parked
  └─ experiment-13: parked
  └─ experiment-14: parked
  └─ experiment-15: parked
  └─ experiment-16: parked
  └─ experiment-17: parked
  └─ experiment-18: parked
  └─ experiment-19: parked
  └─ experiment-20: parked
independent / restored branches
  └─ source: parked
saved indexed ← add-index [ready]
saved seed ← source [ready]
```

Observed: 0 clusters, 0 PostgreSQL processes, 0.0 MiB summed RSS.
Store apparent bytes: 17,961,897,337; allocated block bytes (not unique): 17,963,966,464.

## Completed: restored branch parked

```text
running clusters: 0/3
independent / restored branches
  └─ restored-index: parked
```

Observed: 0 clusters, 0 PostgreSQL processes, 0.0 MiB summed RSS.
Store apparent bytes: 768,991,705; allocated block bytes (not unique): 785,461,248.

## Evidence and limits

[Commands](commands.jsonl) · [SQL results](queries.jsonl) · [Snapshots and storage](snapshots.json)

Source/cache objects, retained generations and WAL are counted in store totals. Export is separate. No exact shared-space or peak-space measurement; no power-loss or competing-product benchmark. The earlier 100-branch benchmark is a separate run.

Snapshots show observed state at each stage, not a live dashboard. Use `python3 scripts/demo.py --inspect RUN_DIRECTORY` for current state.
