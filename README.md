# forklift

Forklift is an experimental Rust CLI for local PostgreSQL 17 branches on macOS.
Each branch has its own database cluster and can be created from a saved checkpoint.

It can bind a branch to a Git worktree and save or restore the database together
with selected source files. Capture requires a clean database shutdown. Pause
agents and unmanaged application writers before capture or restore. Opt-in
[managed lifecycle hooks](docs/project.md#opt-in-managed-app-lifecycle) can drain
and resume an owned app/worker group. External services and side effects are not captured. PostgreSQL's durability settings remain enabled.

## Requirements and installation

- Apple Silicon Mac with an APFS volume. Other platforms are unsupported;
  `--copy-mode full` changes the copy backend, not the supported platform.
- Native PostgreSQL 17 (tested with 17.11), Git and Python 3.9+ for the examples/tests.
- Rust installed through [rustup](https://rustup.rs/). The repository pins Rust 1.95.0 and its tools.
- Application compatibility: a PostgreSQL connection URL, using Unix sockets or
  opt-in password-authenticated loopback TCP. Projects can select a database name.
  The PostgreSQL backend supports `plpgsql` 1.0, `citext` 1.6, `hstore` 1.8, `uuid-ossp` 1.1,
  `pg_trgm` 1.6 and separately installed pgvector (`vector` 0.8.0). Other extensions
  remain unsupported; see the [compatibility rules](docs/architecture.md#compatibility-and-limits).

```sh
git clone https://github.com/alanagoyal/forklift.git
cd forklift
brew install postgresql@17
export PATH="$HOME/.cargo/bin:$PATH"
cargo install --path . --locked --bin forklift
forklift --help
```

Forklift creates and manages its own clusters. You do not need to start Homebrew's
default database service. The PostgreSQL binary directory defaults to
`/opt/homebrew/opt/postgresql@17/bin`; set `PG_BIN` for scripts or `--pg-bin` for the
CLI if yours is elsewhere. Never point Forklift at an existing production data directory.

## Try a code and database rewind

From this repository, run:

```sh
python3 scripts/quickstart.py benchmark-data/my-first-rewind
```

This creates a small invoice application in a new Git repository and an isolated
database store. It captures a baseline, changes the application version, adds a
database column and deletes an invoice, then restores the baseline. These are
deterministic demo edits; no coding agent, API key or BB installation is needed.

Expected output includes:

```text
before: version 1, 3 invoices, $175.00 outstanding
experiment: version 2, 2 invoices, $125.00 outstanding
restored: version 1, 3 invoices, $175.00 outstanding
All demo databases are stopped.
```

The script verifies the restored source bytes, schema and rows, and stops all its
databases on success or ordinary errors. Use a new output path for each run.
The [sample app and configuration](examples/quickstart) are small enough to read
in full. Exact commands and results are saved in the output's `commands.json`.

To keep experimenting with the generated project:

```sh
cd benchmark-data/my-first-rewind/app
forklift project task report
forklift project capture before_change
# Edit app.py and run database commands through forklift project run -- COMMAND ...
# Stop application commands and pause agents/writers before rewinding.
forklift project stop
forklift project restore before_change
forklift project task report
forklift project stop
```

`project stop` parks the current database, freeing a server slot for restore.
Application commands start it again as needed. Restore otherwise needs a spare
slot; the default limit is three running clusters per store. If it refuses at
capacity, no restore has begun. An interrupted restore requires `project recover`;
see the [project recovery instructions](docs/project.md#interrupted-restore).

The demo's app, store and snapshots remain in its output directory after stopping.
Once the script has succeeded and you no longer need the experiment, you can remove
that entire disposable output directory. If you restarted it, run `project stop`
first. Do not delete files from inside a store to reclaim space.

## Use your own project

Follow the [project setup guide](docs/project.md) to import a trusted development
dump, choose owned source paths in `.forklift.json`, and bind a Git worktree to a
database branch. Run `forklift project doctor` to check setup and local connectivity.
For Supabase projects, the optional [native Supabase backend](docs/project.md#native-supabase-projects)
sets up a private local stack and rewinds its database, Auth records and Storage files.
Plain PostgreSQL projects require no Supabase dependency. Selected dirty and untracked files are captured; Git's index and
unselected files are preserved. Each additional worktree can run `forklift project
setup` to get its own branch. Ordinary Git worktrees work without an IDE integration.

BB is an agentic IDE that manages projects and worktrees. Its optional
[setup/teardown hooks](docs/project.md#bb-worktree-hooks) call the same CLI.
The adapter can coordinate an explicitly configured app supervisor through
`project app-start` / `app-stop`; it does not automatically pause coding agents.

For database-only branching, see the [CLI guide](docs/cli.md). For a separate
owned source directory rather than a stable Git worktree, see [`forklift pair`](docs/cli.md#paired-application-workspaces).

## Limits

- Retention is explicit: `project forget NAME` removes a saved capture, and
  `project prune` reclaims obsolete source/database generations and unused objects.
  Both preview by default; add `--yes` to apply. Standalone stores also provide
  named deletion, `cleanup` and `gc`. Legacy resources without ownership tracking
  remain protected; automatic retention policies are unsupported. Stores are limited
  to 1,000 named branches/checkpoints. See [retention](docs/cli.md#explicit-retention).
- Captures require a clean database shutdown. Cooperating application commands and
  unmanaged agents must be paused; opted-in app hooks drain work before capture.
  Live noninterrupting snapshots are unsupported.
- Selected source supports regular files/directories only: at most 10,000 entries,
  16 MiB per file and depth 64. No symlinks, hardlinks or nested exclusions.
- Checkpoint export requires a compatible PostgreSQL version, build and platform.
  Stores pin their PostgreSQL installation; automatic upgrades are unsupported.
- No cloud service, database merging, hosted auth/storage adapter or arbitrary
  host power-loss guarantee. This is a local development tool.

## Documentation

- [Project guide](docs/project.md): PostgreSQL and Supabase setup, app hooks, worktrees, recovery and retention.
- [CLI reference](docs/cli.md): database branches and separate paired source workspaces.
- [Architecture](docs/architecture.md): storage, checkpoint format, compatibility and durability.
- [Benchmarks](docs/benchmarks.md): reproduction commands, methodology and recorded evidence.

Run the deterministic test suite with `python3 scripts/test.py`. See
[CONTRIBUTING.md](CONTRIBUTING.md) for prerequisites, focused checks and the code map.
Forklift is [MIT licensed](LICENSE).

PostgreSQL checkpoints can also use [private ordinary Git storage](docs/git-storage.md).
Select it with `forklift --home STORE config --storage-backend git`; existing
checkpoints retain their backend, and chunked storage remains the default.
