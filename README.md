# forklift

Local Postgres branches for experiments.

**Status:** milestone three implemented. Forklift manages local PostgreSQL 17
branches and saves portable, compressed object checkpoints with content reuse,
checksums, export and restore. PostgreSQL still runs on ordinary local files.

Start with the [CLI walkthrough](docs/lifecycle.md), then see the
[object format and portability contract](docs/checkpoint-format.md) and
[milestone-three validation report](docs/milestone-three.md).
Earlier reports cover [lifecycle](docs/milestone-two.md) and
[initial feasibility](docs/milestone-one.md). The [project plan](PROJECT_PLAN.md)
and [architecture decision](docs/architecture-decision.md) distinguish implemented
behavior from future work.

This is local object storage, not a cloud service or an object-backed query engine.
Running-server limits and many-branch measurements are next in milestone four.

## Reproduce the experiment

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
