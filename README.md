# forklift

forklift is an experimental rust cli for local postgresql 17 branches on macos.
each branch has its own database cluster and can be created from a saved checkpoint.

it can bind a branch to a git worktree and save or restore the database together
with selected source files. capture requires a clean database shutdown. pause
agents and unmanaged application writers before capture or restore. opt-in
[managed lifecycle hooks](docs/project.md#opt-in-managed-app-lifecycle) can drain
and resume an owned app/worker group. external services and side effects are not captured. postgresql's durability settings remain enabled.

## requirements and installation

- apple silicon mac with an apfs volume. other platforms are unsupported;
  `--copy-mode full` changes the copy backend, not the supported platform.
- native postgresql 17 (tested with 17.11), git and python 3.9+ for the examples/tests.
- rust installed through [rustup](https://rustup.rs/). the repository pins rust 1.95.0 and its tools.
- application compatibility: a postgresql connection url, using unix sockets or
  opt-in password-authenticated loopback tcp. projects can select a database name.
  the postgresql backend supports `plpgsql` 1.0, `citext` 1.6, `hstore` 1.8, `uuid-ossp` 1.1,
  `pg_trgm` 1.6 and separately installed pgvector (`vector` 0.8.0). other extensions
  remain unsupported; see the [compatibility rules](docs/architecture.md#compatibility-and-limits).

```sh
git clone https://github.com/alanagoyal/forklift.git
cd forklift
brew install postgresql@17
export PATH="$HOME/.cargo/bin:$PATH"
cargo install --path . --locked --bin forklift
forklift --help
```

forklift creates and manages its own clusters. you do not need to start homebrew's
default database service. the postgresql binary directory defaults to
`/opt/homebrew/opt/postgresql@17/bin`; set `PG_BIN` for scripts or `--pg-bin` for the
cli if yours is elsewhere. never point forklift at an existing production data directory.

## try a code and database rewind

from this repository, run:

```sh
python3 scripts/quickstart.py benchmark-data/my-first-rewind
```

this creates a small invoice application in a new git repository and an isolated
database store. it captures a baseline, changes the application version, adds a
database column and deletes an invoice, then restores the baseline. these are
deterministic demo edits; no coding agent, api key or bb installation is needed.

expected output includes:

```text
before: version 1, 3 invoices, $175.00 outstanding
experiment: version 2, 2 invoices, $125.00 outstanding
restored: version 1, 3 invoices, $175.00 outstanding
All demo databases are stopped.
```

the script verifies the restored source bytes, schema and rows, and stops all its
databases on success or ordinary errors. use a new output path for each run.
the [sample app and configuration](examples/quickstart) are small enough to read
in full. exact commands and results are saved in the output's `commands.json`.

to keep experimenting with the generated project:

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
application commands start it again as needed. restore otherwise needs a spare
slot; the default limit is three running clusters per store. if it refuses at
capacity, no restore has begun. an interrupted restore requires `project recover`;
see the [project recovery instructions](docs/project.md#interrupted-restore).

the demo's app, store and snapshots remain in its output directory after stopping.
once the script has succeeded and you no longer need the experiment, you can remove
that entire disposable output directory. if you restarted it, run `project stop`
first. do not delete files from inside a store to reclaim space.

## use your own project

follow the [project setup guide](docs/project.md) to import a trusted development
dump, choose owned source paths in `.forklift.json`, and bind a git worktree to a
database branch. run `forklift project doctor` to check setup and local connectivity.
for supabase projects, the optional [native supabase backend](docs/project.md#native-supabase-projects)
sets up a private local stack and rewinds its database, auth records and storage files.
plain postgresql projects require no supabase dependency. selected dirty and untracked files are captured; git's index and
unselected files are preserved. each additional worktree can run `forklift project
setup` to get its own branch. ordinary git worktrees work without an ide integration.

bb is an agentic ide that manages projects and worktrees. its optional
[setup/teardown hooks](docs/project.md#bb-worktree-hooks) call the same cli.
the adapter can coordinate an explicitly configured app supervisor through
`project app-start` / `app-stop`; it does not automatically pause coding agents.

for database-only branching, see the [cli guide](docs/cli.md). for a separate
owned source directory rather than a stable git worktree, see [`forklift pair`](docs/cli.md#paired-application-workspaces).

## limits

- retention is explicit: `project forget NAME` removes a saved capture, and
  `project prune` reclaims obsolete source/database generations and unused objects.
  both preview by default; add `--yes` to apply. standalone stores also provide
  named deletion, `cleanup` and `gc`. legacy resources without ownership tracking
  remain protected; automatic retention policies are unsupported. stores are limited
  to 1,000 named branches/checkpoints. see [retention](docs/cli.md#explicit-retention).
- captures require a clean database shutdown. cooperating application commands and
  unmanaged agents must be paused; opted-in app hooks drain work before capture.
  live noninterrupting snapshots are unsupported.
- selected source supports regular files/directories only: at most 10,000 entries,
  16 mib per file and depth 64. no symlinks, hardlinks or nested exclusions.
- checkpoint export requires a compatible postgresql version, build and platform.
  stores pin their postgresql installation; automatic upgrades are unsupported.
- no cloud service, database merging, hosted auth/storage adapter or arbitrary
  host power-loss guarantee. this is a local development tool.

## documentation

- [project guide](docs/project.md): postgresql and supabase setup, app hooks, worktrees, recovery and retention.
- [cli reference](docs/cli.md): database branches and separate paired source workspaces.
- [architecture](docs/architecture.md): storage, checkpoint format, compatibility and durability.
- [benchmarks](docs/benchmarks.md): reproduction commands, methodology and recorded evidence.

run the deterministic test suite with `python3 scripts/test.py`. see
[`CONTRIBUTING.md`](CONTRIBUTING.md) for prerequisites, focused checks and the code map.
forklift is [mit licensed](LICENSE).
