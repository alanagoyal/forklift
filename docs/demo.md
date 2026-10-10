# run the complete Forklift demo

This demo creates 20 isolated database experiments, runs three, resets one, and
moves a saved checkpoint into a separate store. It uses real PostgreSQL and records
every CLI operation, SQL check, query plan and resource snapshot.

## Setup

Supported: macOS ARM64 on APFS, PostgreSQL 17, Rust 1.95.0 and Python 3. The recorded
runs use Homebrew PostgreSQL 17.11. Export/restore currently requires an identical
PostgreSQL version/build and platform. This is a local development tool.

From the repository root:

```sh
brew install postgresql@17
export PATH="$HOME/.cargo/bin:$PATH"
cargo build --locked --release
python3 scripts/demo.py --output benchmark-data/my-demo --rows 1000000
```

Install Rust through rustup if it is not available; `rust-toolchain.toml` pins the
version. Fetch/build dependencies before an offline demo. Homebrew's background
PostgreSQL service is not needed. Each demo server uses private Unix sockets with
TCP disabled. The script never accepts an external database as its source.

`--output` must be a new directory. Use `--rows 100000` for a shorter run. The
fixture is deterministic and deliberately compressible; it is not a production
workload. Keep several GB of disk space available for retained data. All clusters
are stopped on successful completion. Failures attempt identity-checked cleanup.
A restrictive sandbox must allow PostgreSQL shared memory and process inspection.

Override binary paths if needed:

```sh
python3 scripts/demo.py --output benchmark-data/another-demo \
  --binary target/release/forklift \
  --pg-bin /opt/homebrew/opt/postgresql@17/bin
```

## What you will see

1. **A saved starting point.** Seed an accounts table, verify durability settings,
   save `seed`, then park the source.
2. **Twenty parked experiments.** The first fork reconstructs a stopped cache;
   the next forks verify and APFS-clone it. A terminal tree shows the branches.
3. **Three running experiments.** Add an index in `add-index`, add a column in
   `change-schema`, and delete 10% of accounts in `delete-rows`. The active limit
   stays at three; the other seventeen experiments remain parked.
4. **A real query-plan comparison.** Five `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)`
   samples before and after the index, with the same query result. No planner
   settings force index use. This demonstrates the effect of a SQL index, not a
   speedup caused by Forklift's storage system.
5. **Undo the destructive experiment.** Reset `delete-rows` to `seed`, then compare
   its complete account-data digest with the original. Other experiments remain
   independent. Check the source's original data, columns and indexes.
6. **Independent portable restore.** Save/export the indexed branch, stop the
   original servers, move the source store out of its recorded location, and
   restore the exported bundle into a new store. Verify both data and the index.
7. **Park everything.** Both stores finish stopped. Save the report and evidence.

Opening a report does not query a running database. Its trees and measurements
are snapshots from the named stages. For a fresh view of current branch states:

```sh
python3 scripts/demo.py --inspect benchmark-data/my-demo
```

Inspection calls `forklift list`, which performs recovery reconciliation and can
resume an interrupted operation. It is not a purely read-only command. Concurrent
CLI operations are serialized; retry an inspection if it encounters a busy lock.
The tree shows checkpoint relationships, active counts and actual PostgreSQL
parent/worker process RSS. It is a terminal view, not a daemon or web dashboard.

## Read the evidence

The run directory contains:

| File | Contents |
| --- | --- |
| `report.md` | Human-readable results, branch trees and measured operation times. |
| `summary.json` | Dataset, verification outcomes, complete query plans and environment. |
| `commands.jsonl` | CLI arguments, whole-command timings, errors and stage events. |
| `queries.jsonl` | SQL statements, results and psql wall times; no fixture row dump. |
| `snapshots.json` | Observed branch/catalog state, process resources and storage counters. |
| `SUCCESS` | Written only when the full workflow and cleanup pass. |
| `indexed.forklift/` | Self-contained portable checkpoint bundle. |
| `source/`, `restored/` | Original experimental store and independent restored store. |

`EXPLAIN` execution time excludes connection setup and CLI overhead. Operation
wall times include them where applicable. Fixed before/after ordering and an
uncontrolled OS file cache mean the query experiment is not a randomized benchmark.

RSS sums can count shared pages repeatedly. Apparent bytes and allocated block
counts cannot establish unique APFS space or exact sharing. Store totals include
cached data, object packs, WAL and previous reset generations; exported data is
counted separately. Failed work remains retained. No power-loss or peak-space
claim follows from this demo.

The separate scale experiment is still reproducible with:

```sh
python3 scripts/benchmark_branches.py
```

It checks 1/5/20/100 stored experiments, zero/one/three active servers, and distinct
saved markers after restarting all 100. Its [milestone-four report](history/milestone-four.md)
is a separate measurement; it is not folded into the demo's timings.

## Recovery and cleanup

After an interrupted run, inspect that run's stores with the script above. Stop
running named branches using the same store path, for example:

```sh
target/release/forklift --home benchmark-data/my-demo/source stop add-index
target/release/forklift --home benchmark-data/my-demo/restored stop restored-index
```

If interruption occurred during the independent-restore demonstration, the
original store may instead be at `source-offline/`; inspection includes that path.
The source was stopped before that move. Do not move any running store.

Forklift refuses mismatched process identity instead of guessing which process to
stop. Inspect such an error before proceeding. Failed branch names cannot be reused;
rerun the demo with a new output directory. Once every store has no running or
pending servers, the entire generated run directory can be removed manually.
There is no automatic garbage collector or per-branch deletion command yet.

See [the lifecycle guide](lifecycle.md) for detailed recovery semantics and
[the final milestone report](history/milestone-five.md) for measured results and remaining
limitations.
