# Benchmarks and reproducible experiments

Start with the [README quickstart](../README.md#try-a-code-and-database-rewind) for
one application rewind. The experiments here use disposable local fixtures and
retain evidence; none imports hosted data. Use macOS ARM64/APFS, the pinned Rust
toolchain, PostgreSQL 17 and Python 3.9+. Run commands from the repository root.

PostgreSQL durability and checksums remain enabled. Run one benchmark at a time,
record versions and executable identity, and stop all owned servers before deleting
an output directory. Timings apply to their recorded workload and environment.
APFS apparent bytes and allocated blocks do not measure unique shared physical
space; summed process RSS can count shared memory more than once. There is no
host power-loss, production-scale, or Neon/DBLab performance claim.

## Retained evidence

| Experiment | Evidence |
| --- | --- |
| Stopped-cluster copy, APFS clone and logical restore | [100k-row run](../results/2026-10-07-100k) |
| Object checkpoint reuse and storage accounting | [Measurement report](../results/2026-10-08-objects/report.md) |
| 100 stored branches, up to three active servers | [Measurement report](../results/2026-10-08-branches/report.md) |
| Complete database demo | [Smoke](../results/2026-10-08-demo/smoke/report.md), [million rows](../results/2026-10-08-demo/million/report.md) |
| Native paired code/database rewind | [Recorded experiment](../results/2026-10-08-rust-paired/report.md) |
| Git worktree rewind | [Validation](../results/2026-10-08-project/report.md), [parallel experiments](../results/2026-10-09-parallel/report.md), [partial payments](../results/2026-10-09-partial-payments-real/report.md) |
| Checkpoint cost comparison | [Report](../results/2026-10-10-checkpoints/report.md) |
| Managed app hooks | [Correctness validation](../results/2026-10-10-managed-app/report.md); no performance claim |

## Checkpoint costs

The checkpoint benchmark measures `project capture` and `project restore` separately
in disposable Git repositories and local database stores. It never imports hosted
data. Native Supabase uses an explicit private PostgreSQL 17 configuration and an
APFS clone of the supplied runtime cache. Booklist's original PG15 configuration,
checked-in migration, and data-loading script are unchanged.

Build a release binary and run one benchmark at a time:

```sh
cargo build --release --features test-failpoints
python3 scripts/benchmark_checkpoints.py --backend postgres \
  --sizes-mib 1,32,128 --samples 3 --output /tmp/checkpoint-postgres
python3 scripts/benchmark_checkpoints.py --backend supabase \
  --supabase-bin /path/to/supabase-2.120.0/supabase \
  --supabase-cache /path/to/native-probe/home/cache \
  --booklist /path/to/booklist \
  --sizes-mib 1,32,128 --samples 3 --output /tmp/checkpoint-supabase
python3 scripts/summarize_checkpoints.py /tmp/checkpoint-postgres/samples.jsonl.gz
```

Output directories must be new. Fixtures remain under ignored `benchmark-data/`
for inspection; the script stops every fixture server in `finally` and records
whether cleanup succeeded. Use `--copy-mode full` for explicit full copies.
Neither mode changes PostgreSQL durability settings. The benchmark requires fsync,
full_page_writes, synchronous_commit, and database page checksums to be on.

Each size starts with a new store/stack. The synthetic database contains 512 rows
per requested MiB, each with 2 KiB of deterministic MD5 text, plus row/index/storage
overhead. Native Storage has one distinct 1 MiB object per requested MiB. Source is
64 files totaling 1 MiB. Booklist adds its unchanged complete migration and two
synthetic books/people; its unchanged dump script runs before measurements and
after each restore. The larger synthetic payload is in a separate schema, so the
Booklist dump remains small. This is not a production-size Booklist workload.

`environment.json` records versions, the measured executable's SHA-256, setup/cache
policy, and cleanup. `samples.jsonl.gz` records dataset sizes/file counts, wall time,
and nested phase spans. Only fixed timing labels and numeric metrics are emitted;
command arguments, SQL, credentials, runtime status, and file paths are excluded
from the timing stream. Fixture location in environment metadata is not a secret.

### Interpreting checkpoint measurements

`FORKLIFT_TIMING=1` emits one JSON record on stderr after the command's work. Normal
stdout and command results are unchanged. Timing is disabled by default. Spans
use monotonic time and overlap when a phase calls another phase. The summarizer
subtracts direct children to report exclusive time; do not sum inclusive spans.
Parallel data-file fsync workers are covered by the parent `tree_fsync` wall time,
not summed worker durations. Source copy includes content copying, permission
updates and validation; its nested source hashes and fsyncs can be separated.
Object capture also supplies its existing hash/compression/reuse/write counters.
These counters overlap spans and must not be added to them.

Total time includes admission, compatibility checks, source work, durability,
publication, and service readiness. The reported service window is a conservative
interval from entering shutdown to completing subsequent startup/readiness. It is
not a sampled client-observed outage: shutdown checks begin while the old service
may still accept requests. A separate lower bound runs from shutdown command
completion to entry into the next start/readiness call, during which services
are known to remain stopped. Native restore validates its saved state before this
window. PostgreSQL restore starts a replacement before parking the old database,
so it has no equivalent all-databases-down interval; the old endpoint still becomes
obsolete and clients must reconnect through the selected project connection.

Application maintenance is different again. These fixtures have no persistent app
server. A cooperating application must stay paused for the entire operation,
including source replacement. Application restart time is **not measured**. The
managed-app implementation was integrated after these measurements; its lifecycle
tests are recorded separately from the benchmark comparison.

First restore of `sample_0` is a PostgreSQL materialization-cache miss; subsequent
restores of that same capture are hits. Capture 0 is first capture of the loaded
fixture, and later captures reuse existing object packs. Native captures are
filesystem snapshots, with no packed-object cache or compression. Runtime artifact
cache is populated before setup; first setup includes database creation, migrations
and enabling checksums. Download time is not measured. No OS page cache purge is
performed: neither "first" nor "cache miss" means cold disk cache. Repeated samples
are warm operational runs, and retained snapshots accumulate as in normal usage.

Plain PostgreSQL currently checks the clean-shutdown control state on capture;
it does not run a separate offline `pg_checksums --check` in that path. Native
Supabase does. Object restore validates chunk and file hashes. Timings describe
these implemented checks, not a claim that both backends run identical checks.

Results and bounded optimization decisions are recorded in
[the measurement report](../results/2026-10-10-checkpoints/report.md).

### Comparing builds

Save the baseline executable before rebuilding, or build the timing-only commit
`111e096` in a separate checkout. Pass `--bin` and `--build-revision` so the receipt
identifies the executable being measured, not just the current source checkout.
Use identical sizes, source counts, copy mode, cache policy and sample order for
both builds. Do not compile, run tests, or run another benchmark during sampling.
Record background contention and repeat affected runs instead of treating them as
an isolated speedup. Historical pilot results are labeled separately.

The measured candidate enables the pinned `sha2` crate's existing ARM backend.
Its [dispatch source](https://docs.rs/sha2/0.10.9/src/sha2/sha256.rs.html) selects
that backend on aarch64 only when the `asm` feature is enabled. The backend tests
CPU support and falls back to software when required. The algorithm, stored
SHA-256 values, artifact deduplication within a scan, and number of reads/checks
are unchanged. No durability call, shutdown check, or publication barrier is
removed. Independent digest vectors and the corruption/recovery suites check
compatibility; performance results belong in the report, not in this guarantee.

## Database demo

This demo creates 20 isolated database experiments, runs three, resets one, and
moves a saved checkpoint into a separate store. It uses real PostgreSQL and records
every CLI operation, SQL check, query plan and resource snapshot.

### Run the demo

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

### What the demo checks

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

### Demo evidence

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
saved markers after restarting all 100. Its [stored-branch measurement report](../results/2026-10-08-branches/report.md)
is a separate measurement; it is not folded into the demo's timings.

### Demo recovery and cleanup

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
Explicit `delete-branch`, `delete-checkpoint`, and `gc` commands preview removal
by default and require `--yes` to apply it. Automatic retention is not implemented.

See [database recovery](cli.md#saved-state-and-recovery) for detailed semantics.

## Coding-agent rewind experiment

Use macOS/APFS with the repository's pinned Rust and PostgreSQL 17 installation.
PostgreSQL needs shared-memory and Unix-socket access. The agent requires a working
Codex CLI login; the provider call may use a network service even though all
Forklift storage and database operations are local. Inspect `codex exec --help`
when using a different CLI version. The runner records its exact argv and prompt,
allows local socket networking in the agent sandbox, and bounds execution to ten
minutes. A timeout kills the invocation's process group; inspect state afterward.

```sh
export PATH="$HOME/.cargo/bin:/opt/homebrew/opt/postgresql@17/bin:$PATH"
cargo build --locked --release
python3 scripts/demo_paired.py prepare benchmark-data/my-pair results/my-pair
python3 scripts/demo_paired.py agent benchmark-data/my-pair results/my-pair
python3 scripts/demo_paired.py finish benchmark-data/my-pair results/my-pair
```

Both output directories must be new. `prepare` seeds three invoices, saves the
baseline and leaves the owned application database running for the agent.
`agent` asks the actual provider to implement and apply invoice archiving;
`finish` independently observes the changes, saves the modified pair, restores
the baseline, asserts exact source/schema/row/application equality, checks source
and sibling isolation, and stops every server in this dedicated demo store.
Model output is nondeterministic: a zero agent exit code alone is not acceptance.
The finish assertions and transcript review establish what actually happened.

Inspect or run the restored application again:

```sh
target/release/forklift pair benchmark-data/my-pair/workspace inspect
target/release/forklift pair benchmark-data/my-pair/workspace run -- python3 app.py
target/release/forklift pair benchmark-data/my-pair/workspace stop
```

After an interruption, first run `forklift pair WORKSPACE recover`. If an experiment
fails before `finish`, use that recovery command and `forklift pair WORKSPACE stop` to
park the selected app. For the dedicated demo store, inspect `forklift --home
benchmark-data/my-pair/store list` and stop any other running owned branches before
removing files. A failed initializer can leave an owned partial store; inspect it
with the Rust CLI rather than retrying into existing output directories.

See [the pair contract](cli.md#paired-application-workspaces) for capture and recovery rules.

## Parallel worktree experiments

These demos run two real coding agents in separate Git worktrees and database
branches, then rewind one while checking that the other and the seed are unchanged.
They require the same authenticated Codex CLI and local PostgreSQL access as the
paired experiment. Both paths must be new:

```sh
cargo build --locked --release
python3 scripts/demo_parallel.py all benchmark-data/my-parallel-run results/my-parallel-run
python3 scripts/demo_partial_payments.py all benchmark-data/my-payments-run results/my-payments-run
```

Each script supports `prepare`, `agents`, `finish` and `cleanup` phases with the
same two paths. `prepare` creates the fixture, worktrees, owned branches and
baselines; `agents` runs the two providers through `project run`. `finish` requires
successful edit/migration receipts, captures both designs, restores one, checks
source/schema/rows, Git index/HEAD, unselected files and sibling isolation, then
runs teardown. The partial-payments finish records a payment once; use a fresh
run for a complete repeat. `FORKLIFT_BIN` and `PG_BIN` override binary locations.

Normal errors attempt owned-server cleanup; timed-out agents are stopped as process
groups first. After an externally killed run, stop any still-running owned agents,
then invoke that script's `cleanup` phase. Agents must finish before capture and
rewind. These tiny CLI fixtures measure correctness, not performance, production
scale, malicious-agent isolation or automatic agent pausing. See the
[project guide](project.md) for the maintained workflow and recovery rules.

## Feasibility copy comparison

```sh
cargo build --locked --release
./target/release/forklift-feasibility benchmark-data/my-run 100000 5 /opt/homebrew/opt/postgresql@17/bin
```

The original feasibility harness uses the standard library; the lifecycle CLI
adds pinned dependencies recorded in Cargo.lock. Fetch dependencies before offline
operation. The supplied run directory must not exist. Start with `1000 1` for a
smoke test. The executable takes: new run directory, row count (1–1,000,000),
repetitions (1–20), and Postgres binary directory. It requires permission to use
PostgreSQL shared memory; a restrictive agent sandbox may require an elevated
execution context. Do not start Homebrew's default database service for this test.

The deterministic fixture uses one table, two initial indexes and no extensions.
Each trial compares explicit full-file reads/writes, native `clonefile` calls
(no fallback), and custom-format `pg_dump`/`pg_restore`. Native paths copy the
whole stopped cluster; logical restore copies the fixture database into a fresh
cluster, so these are workflow baselines, not identical internals.

Each branch gets a migration and updates; a sibling gets a conflicting migration
and deletes. The harness compares data and schema digests, restarts both branches,
checks the unchanged seed, and checks PostgreSQL's on-disk page checksums.
Success prints the results location and leaves all clusters stopped.

`metrics.tsv`, `environment.txt`, `checksums.txt`, process snapshots, logs and a
`SUCCESS` marker are written into the run directory. Raw cluster files and dumps
stay under ignored `benchmark-data/`; small evidence files are committed under
`results/`. Runs retain all copies and can consume several GB. No server listens
on TCP; Unix sockets are private to the run. The harness never accepts an existing
external database as input and does not touch another project.

On normal errors it attempts cleanup. After a force-killed run, inspect only
that run's `postmaster.pid` files and stop any remaining clusters with:

```sh
/opt/homebrew/opt/postgresql@17/bin/pg_ctl -D benchmark-data/my-run/CLUSTER -m fast -w stop
```

Replace `CLUSTER` with the specific directory from that run. Verify its identity
before stopping it. Once all are stopped, the run directory can be removed.
This manual recovery applies only to the feasibility harness. The lifecycle CLI
uses its catalog to reconcile interrupted operations on the next command.
