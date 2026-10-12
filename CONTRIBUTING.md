# Contributing to Forklift

Forklift is an experimental local development tool. Bug reports, documentation
improvements, and focused pull requests are welcome. Open an issue before a large
change so we can agree on its scope. Include your macOS, Rust and PostgreSQL
versions, the command you ran, and a small reproduction using disposable data.
Remove credentials, private source and database contents from reports and logs.

## Development setup

Use an Apple Silicon Mac with APFS, native PostgreSQL 17, Python 3.9+ and Git.
Install Rust through [rustup](https://rustup.rs/); `rust-toolchain.toml` selects the
toolchain and tools.

```sh
brew install postgresql@17
export PATH="$HOME/.cargo/bin:$PATH"
python3 scripts/test.py
```

This is the same command used by [CI](.github/workflows/test.yml). It runs formatting,
Rust unit tests and Clippy, then real PostgreSQL lifecycle, object-integrity,
extensions, cache, cleanup, retention, paired and Git-project suites, including
named databases and password-authenticated TCP. It also creates workspaces/stores using historical
implementations and checks compatibility, and verifies that normal release builds
ignore the PostgreSQL failure-injection environment variables. The README quickstart
also runs against the normal release binary. It needs no BB, coding
agent, API credentials or hosted database. Initial dependency downloads need network
access; the tests run locally.

Use a full Git clone: compatibility checks read commits `f0e1042` and `36cf232`.
For a shallow clone, run `git fetch --unshallow` first. The runner builds the old
implementation in a temporary directory without switching your checkout.

Set `PG_BIN` to override the default `/opt/homebrew/opt/postgresql@17/bin`.
The tests create fresh stores under ignored `benchmark-data/` and require local
Unix sockets and PostgreSQL shared memory; an agent sandbox may need permission
to access those facilities. Do not start a shared Homebrew database service for
them. Never run tests against a production or personal database. PostgreSQL
durability remains enabled throughout.

Allow several minutes and several GB of free space. Suites stop their servers and
retain their stores and evidence; `forklift cleanup` can preview disposable cache
and unreferenced database generations within a store. The runner
prints a log directory under `benchmark-data/test-runs/` with a JSON summary and
per-step logs. It exits nonzero on the first failure. CI uploads those logs, without
raw database clusters. After a forced interruption, inspect the affected store with
`forklift --home STORE list` and stop its running branches before removing files;
see [recovery](docs/cli.md#saved-state-and-recovery).

## Focused checks

For a small Rust change, start with:

```sh
cargo fmt --check
cargo test --locked
cargo clippy --locked --all-targets --all-features -- -D warnings
```

For an individual integration suite, build an explicit test binary:

```sh
cargo build --locked --release --bin forklift --features test-failpoints --target-dir target/acceptance/failpoints
FORKLIFT_BIN="$PWD/target/acceptance/failpoints/release/forklift" python3 scripts/test_cleanup.py
```

The full runner uses separate `target/acceptance/failpoints` and
`target/acceptance/release` directories. Install a normal release with
`cargo install --path . --locked --bin forklift`; never install a build with
`test-failpoints` enabled. Recorded results identify the builds measured; use this
guide for current checks.

The optional Booklist/PostgREST compatibility check needs an existing
Booklist checkout, PostgREST and pgvector 0.8.0 installed for PostgreSQL 17. Set both
`BOOKLIST_ROOT` and `POSTGREST_BIN` to include it in `scripts/test.py`; otherwise
the core suite has no dependency on Booklist or hosted Supabase. It uses synthetic
data and copied application code, never production credentials or rows.

The Booklist check copies an explicit application-file allowlist, uses synthetic
rows and runs the existing dump/search code through PostgREST with a local anon
JWT. It tests RLS, vector search and code/database rewind; it does not validate
hosted services or a complete production build. Reproduce it with:

```sh
cargo build --locked --bin forklift
BOOKLIST_ROOT=/path/to/booklist \
POSTGREST_BIN=/path/to/postgrest \
PG_BIN=/opt/homebrew/opt/postgresql@17/bin \
DYLD_LIBRARY_PATH=/opt/homebrew/opt/postgresql@17/lib/postgresql \
python3 scripts/test_booklist.py
```

Use PostgREST 16.4 and PG17 extensions `vector` 0.8.0, `pg_trgm` 1.6 and
`uuid-ossp` 1.1. Install the Booklist checkout's dependencies beforehand; the test
never reads dotenv files or runs npm install. `DYLD_LIBRARY_PATH` lets the macOS
PostgREST binary use this PostgreSQL installation's `libpq`. Artifacts and a
`SUCCESS` receipt remain under ignored `benchmark-data/booklist-*`; HTTP servers
and the owned database are stopped on exit.

## Code map

- `src/bin/forklift.rs`: command-line arguments and user-facing output.
- `src/lifecycle.rs`: database branches, process identity, catalog and recovery.
- `src/objects.rs`, `src/cache.rs`, `src/storage.rs`: checkpoint packs, verified
  materializations and filesystem operations. PostgreSQL uses ordinary local files.
- `src/paired.rs`: selecting matching application-source and database generations.
- `src/project.rs`: applying selected source paths at a stable Git worktree path.
- `scripts/test_*.py`: independent checks against actual PostgreSQL and Git.
- `src/main.rs`: the historical feasibility benchmark, a separate executable.

## Change guidelines

Keep commits focused, with entirely lowercase subjects and bodies. Describe the
user-visible change and relevant validation in your pull request. Use plain
language when explaining Rust or storage behavior.

Keep `fsync`, `full_page_writes`, `synchronous_commit` and data checksums enabled.
Do not weaken process identity, file ownership or recovery checks to make tests
pass. Add a regression test for a correctness fix, including what happens after an
interruption when relevant. Distinguish implemented behavior from plans and measured
results. Process-exit tests do not establish arbitrary host power-loss safety.

Retain historical evidence as historical evidence; do not rewrite old reports to
claim a new run. Keep generated clusters, local configuration and credentials out
of commits. Test directories remain on disk; explicit cleanup does not remove named branches,
checkpoints, immutable objects or evidence. Explicit named deletion and coordinator
pruning are separate operations; see [retention](docs/cli.md#explicit-retention).
Historical compatibility tests use specific Git commits, so keep full history
available when running them.

Contributions are provided under the project's [MIT license](LICENSE).

## Native Supabase backend checks

The PostgreSQL suite remains independent of Supabase. For the optional native
backend, install the pinned Supabase CLI 2.120.0 and run:

```sh
cargo build --locked --release --bin forklift --features test-failpoints --target-dir target/supabase-tests
FORKLIFT_BIN="$PWD/target/supabase-tests/release/forklift" \
SUPABASE_BIN=/absolute/path/to/supabase \
python3 scripts/test_supabase.py
```

The first start downloads native artifacts; allow network access and several GB
of space. `BOOKLIST_ROOT=/path/to/booklist` adds the real checked-in migration and
unchanged data-dump integration with synthetic rows. `SUPABASE_TEST_CACHE` can
point to a previously downloaded native runtime's `home/cache` for an APFS-cloned
test cache. Every fixture has private `SUPABASE_HOME`, local credentials, and stops
its services in teardown. No hosted Supabase project is needed or modified.

Setting `SUPABASE_BIN` also includes this suite in `scripts/test.py`. Run the
PostgreSQL suite without that variable to verify there is no Supabase dependency.

## Managed application lifecycle checks

`scripts/test_managed.py` runs a real loopback HTTP app plus a worker that commits
a database value and writes it to source. It tests capture/restore while running,
drain consistency, failed stop/status/start/resume, partial startup, hook timeouts,
concurrent exclusion, parent and restore interruptions, unowned work, and another
running Git worktree. The fixture uses private control state and never kills app
processes by PID. PostgreSQL durability and checksums are asserted enabled.

```sh
FORKLIFT_BIN="$PWD/target/acceptance/failpoints/release/forklift" python3 scripts/test_managed.py
```

For the same lifecycle tests against disposable native Supabase stacks, add
`--supabase`, `SUPABASE_BIN`, and optionally `SUPABASE_TEST_CACHE` as described
above. Neither mode reads Booklist or hosted data. The test writes a `SUCCESS`
report under `benchmark-data/managed-*/` or `managed-native-*/` and stops its
owned apps/services on exit. The full runner includes the PostgreSQL mode and,
when `SUPABASE_BIN` is supplied, the native mode.

Use the current suites and CI to check correctness; historical test logs are not
committed. The normal release check also verifies that managed failure-injection
variables have no effect.
