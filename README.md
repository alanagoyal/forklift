# forklift

Local Postgres branches for experiments.

**Status:** milestones one through eight implemented and validated. Forklift manages
isolated PostgreSQL 17 experiments with parked/running branches, verified cached
APFS clones, portable object checkpoints and a configurable running limit.
PostgreSQL still reads and writes ordinary local files.

## Rewind application code and data together

`forklift pair` captures an owned application source tree with a database checkpoint,
then restores a fresh matching pair. The coordinator is native Rust; Python scripts
only drive the demo and independent tests. Workspaces created by the milestone-six
Python prototype remain compatible. This requires cooperative synchronous writers;
it does not rewind external side effects or arbitrary running applications.

Read the [paired workspace commands and recovery contract](docs/paired.md),
[original real-agent experiment](results/2026-10-08-paired/report.md), and
[Rust integration report](docs/milestone-seven.md).

## Use an existing project or BB worktree

`forklift project` binds a stable Git worktree to its own database branch. A JSON
configuration selects owned source paths and app commands. Capture/restore includes
selected dirty and untracked files while preserving Git's index and unselected
files. Pause the agent and stop application writers before rewinding.

Start with the [project setup and BB workflow](docs/project.md) and
[milestone-eight validation](docs/milestone-eight.md). This adapter supports projects
that can use compatible local PostgreSQL 17 through a Unix-socket URL; it does not
provide hosted auth/storage services or automatically coordinate active BB agents.

## Run the complete demo

On macOS ARM64 with APFS, Rust 1.95.0, Python 3 and native PostgreSQL 17:

```sh
brew install postgresql@17
export PATH="$HOME/.cargo/bin:$PATH"
cargo build --locked --release
python3 scripts/demo.py --output benchmark-data/my-demo --rows 1000000
```

The output directory must be new. The demo creates 20 branches, runs an index
experiment, a schema change and a deletion, resets the deletion, and restores an
export with the original store unavailable. It produces a Markdown report with
branch trees and measured results, then leaves all servers stopped. Use
`--rows 100000` for a shorter run.

Read the [demo walkthrough and recovery instructions](docs/demo.md),
[final milestone report](docs/milestone-five.md), or
[CLI guide](docs/lifecycle.md). For current demo branch states and process resources:

```sh
python3 scripts/demo.py --inspect benchmark-data/my-demo
```

The separate many-branch benchmark is reproducible with
`python3 scripts/benchmark_branches.py`. It verifies a distinct saved marker after
restarting all 100 experiment branches, with at most three servers active at once.

## What is implemented

- Local, offline experiments on whole PostgreSQL clusters.
- Start/stop, import, checkpoint, fork, reset, export and independent restore.
- A verified stopped cache for APFS cloning; explicit full-copy/direct-object paths.
- A per-store running limit (default three), process identity checks and interrupted-operation recovery.
- Immutable compressed checkpoint packs, integrity checks and compatible-build portability.

There is no cloud service, branch merging, live snapshot capture, automatic
startup on connection, garbage collection or custom object-backed query engine.
Old generations and failed work remain retained. Exact unique APFS storage savings
are unmeasured. This is a local development tool, not production infrastructure.

See the [project plan](PROJECT_PLAN.md), [architecture decision](docs/architecture-decision.md)
and [object/cache contract](docs/checkpoint-format.md). Historical evidence covers
[feasibility](docs/milestone-one.md), [lifecycle](docs/milestone-two.md),
[objects](docs/milestone-three.md), and [100 stored branches](docs/milestone-four.md).
No measured advantage over Neon or DBLab is claimed.

## Reproduce the original feasibility experiment

Supported and tested: macOS ARM64, APFS, Rust 1.95.0, PostgreSQL 17 (measured with
17.11). Install a Rust toolchain through rustup and native Postgres, then run
from this repository's root:

```sh
brew install postgresql@17
export PATH="$HOME/.cargo/bin:$PATH"
cargo test --locked
cargo clippy --locked --all-targets -- -D warnings
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
This manual recovery is specific to the milestone-one harness. The lifecycle CLI
uses its catalog to reconcile interrupted operations on the next command.

The repository is private. The project name is a working name, not a trademark
or package-availability claim. This project is separate from greplcia.
