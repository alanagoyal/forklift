# milestone five: reproducible demo and project assessment

> Historical document. Plans, commands, limitations and measurements describe
> the development stage recorded here. See the [history index](README.md) for
> context and links to current documentation.

The complete local experiment workflow is now scripted in
[`scripts/demo.py`](../../scripts/demo.py). It builds a deterministic database,
creates 20 branches, runs three isolated experiments, compares an actual query
plan before/after an index, resets deleted data, and exports/restores a checkpoint
while the source store is unavailable. It records evidence and parks every server.

The [demo guide](../demo.md) covers setup, commands, inspection, artifacts and recovery.
The CLI implementation and storage format are unchanged in this milestone. The
new work is the reproducible demonstration, current-state terminal tree, recorded
SQL experiments and consolidated documentation.

## Recorded runs

Both runs passed on 2026-10-08: 100,000 rows followed by 1,000,000 rows, each
with 20 experimental branches plus one source and an independent restored branch.
The host was the same Apple M4 / 16 GiB RAM / macOS 26.6.2 APFS machine used for
M4, running PostgreSQL 17.11 and the unchanged normal release binary. All four
durability/checksum settings were verified on. Each run made 80 successful CLI
calls. Existing-output refusal and current-state inspection were also checked.

| Measurement | 100,000 rows | 1,000,000 rows |
| --- | ---: | ---: |
| Application database size (MB) | 41.220 | 340.834 |
| Stopped cache size, including WAL/system files (MB) | 107.734 | 776.447 |
| First fork, including cache construction (s; one each) | 5.050 | 9.096 |
| Subsequent cached forks (s; median of 19) | 0.380 | 1.492 |
| Running reset (s; one each) | 0.835 | 1.919 |
| Export (s; one each) | 0.454 | 2.433 |
| Restore to a parked branch (s; one each) | 5.167 | 10.194 |
| Query before index (ms; median of 5) | 5.425 | 21.853 |
| Query after index (ms; median of 5) | 0.022 | 0.068 |
| Three active clusters, summed process RSS (MiB) | 103.266 | 94.516 |

Operation times are whole CLI wall times. Initial cache construction is included
in the first fork, not in the subsequent-fork median. These are small fixed-order
samples, not cold-disk tests or production capacity measurements. The larger
fixture is about 341 MB of application data, not a multi-terabyte workload.

The smaller query used a sequential scan before the index. The larger query used
a parallel sequential scan (with gather/aggregate nodes). Both switched to an
index-only scan. A roughly 322-fold reduction in the larger query's observed
execution time is specific to this selective query and fixture; it is not a
Forklift performance multiplier.

The larger run deleted 100,000 rows in one branch and verified all 1,000,000 after
reset. Its source checkpoint interrupted the source for 7.031 s; saving the
indexed experiment interrupted that branch for 6.038 s. These costs are part of
the workflow, not hidden behind the faster cached-fork number.

The million-row cache held 776.4 MB apparent, including retained WAL and system
files beyond the 340.8 MB application database. That full cached content is
verified for each fork: a warm reset recorded 1.304 s verification and 0.118 s
clone/sync plus destination checks. This illustrates the full-scan cost discussed
in M4; it does not isolate database size from other filesystem/cache effects.

After all operations the original million-row store reported 17.962 GB apparent,
including the cache, 20 experiments, source, old reset generation and object
repository. Its allocated block counter was 17.964 GB, **not unique physical APFS
space**. The exported bundle was 109.6 MB, separate from both stores. Three active
clusters had 18 processes; all parked snapshots had zero PostgreSQL processes.
These single resource observations do not include OS filesystem-cache memory.

Full reproducible reports and raw evidence:

- [100,000-row run](../../results/2026-10-08-demo/smoke/report.md)
- [1,000,000-row run](../../results/2026-10-08-demo/million/report.md)
- [Environment and validation receipt](../../results/2026-10-08-demo/environment.txt)


The query experiment selects accounts with `balance < 100` and computes their
balance sum. The fixture's balances distribute the matches across the table.
We run five `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` samples before adding a
balance index, create the index and run `ANALYZE`, then repeat five samples with
the same SQL and assert the result is unchanged. The planner chooses the index
without forced scan settings. Full plans and buffer counters are in the evidence.

This is an illustration of an ordinary PostgreSQL index reducing query work.
It is **not** a Forklift query-engine optimization or a comparative storage
benchmark. Before/after order is fixed, the OS cache is uncontrolled, and query
execution time excludes client startup, connection and Forklift CLI work.

## What correctness means here

The script checks that:

- Exactly 20 experiment branches begin parked, plus the parked source.
- Resource snapshots agree with catalog activity and never exceed three running clusters.
- Adding an index changes no account data; another branch's new column stays isolated.
- Deleting 10% of rows changes only that branch, and reset restores the original digest.
- The source retains its original account digest and has neither experimental index nor column.
- The exported checkpoint restores with the original store unavailable, retaining data and index.
- All managed demo servers finish stopped and no database pidfiles remain.

The state/resource tree is based on actual observations. Stage trees in the report
are snapshots; `--inspect RUN_DIRECTORY` asks the CLI for current state and counts
its PostgreSQL process descendants. This is not a continuously updating dashboard.
Like other CLI commands, inspection can reconcile interrupted work.

The separate [100-branch benchmark](milestone-four.md) previously verified a
unique saved marker after restarting every branch. Its 1/5/20/100-stored and
0/1/3-active measurements still apply to the unchanged engine; this milestone
does not present those older numbers as a fresh run.

## The project's practical distinction

Forklift packages a short, offline Mac workflow around unmodified PostgreSQL:
keep many named experiments, activate only a few, make conflicting changes, reset
them, and move verified saved state to another compatible local installation.
The catalog and recovery protocol make those steps repeatable without manually
tracking data directories, sockets, copy operations and PostgreSQL processes.

The storage techniques themselves are established: filesystem copy-on-write,
content-addressed chunks, compression, and atomic publication. This project has
not demonstrated a new storage algorithm or a measured win over Neon/DBLab. The
small-fixture M4 cached full-copy baseline was similar to APFS clone latency;
we report that result rather than attributing the entire speedup to cloning.

## Supported scope and remaining limits

- **Platform:** tested on macOS ARM64/APFS with PostgreSQL 17.11. Catalogs pin the
  binary location; portable checkpoints require matching version/build/platform.
- **Snapshots:** a new checkpoint cleanly stops its source. Existing saved checkpoints
  can be forked without stopping that source again. There are no live snapshots.
- **Resources:** the default cap is three clusters per store; it is not a host-wide
  limit. Workers are additional OS processes. Activation is explicit.
- **Storage:** active PostgreSQL uses normal files. Objects save checkpoint state;
  they do not serve query-time database pages. Cache hits still hash all files,
  so verification cost grows with database size.
- **Accounting:** RSS may count shared pages repeatedly. File sizes and `st_blocks`
  do not identify unique APFS physical space. Peak space is not measured. Exports
  are counted separately from stores; old generations and failed work are retained.
- **Recovery:** file/catalog synchronization and PostgreSQL durability remain enabled.
  Prior acceptance suites test interrupted commands and WAL recovery. No host
  power-loss experiment or disk-loss protection is demonstrated.
- **Operations:** no branch merge, per-branch delete, garbage collection, cloud transport,
  connection proxy, production tenant isolation, or high availability. Trusted local
  SQL/dumps only; this is not a sandbox for hostile code or another OS user.

The five planned milestones establish a working prototype and reproducible
measurements. Further work should be chosen against an actual user need: larger
size-scaling experiments, safe cleanup, or deeper comparative evaluation. A custom
storage engine is not required to complete this plan. The repository remains private.
