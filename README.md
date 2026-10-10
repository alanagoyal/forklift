# forklift

Forklift is an experimental Rust CLI for local PostgreSQL 17 branches on macOS.
Each branch has its own database cluster and can be created from a saved checkpoint.

It can bind a branch to a Git worktree and save or restore the database together
with selected source files. Capture requires a clean database shutdown. Pause
agents and application writers before capture or restore; external services and
side effects are not captured. PostgreSQL's durability settings remain enabled.

## Requirements and installation

- Apple Silicon Mac with an APFS volume. Other platforms are unsupported;
  `--copy-mode full` changes the copy backend, not the supported platform.
- Native PostgreSQL 17 (tested with 17.11), Git and Python 3.9+ for the examples/tests.
- Rust installed through [rustup](https://rustup.rs/). The repository pins Rust 1.95.0 and its tools.
- Application compatibility: a driver accepting a PostgreSQL Unix-socket URL and
  the `postgres` development database. **Only `plpgsql` 1.0 is supported.** Any other
  installed extension prevents checkpoint creation and paired/project capture.

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
database branch. Selected dirty and untracked files are captured; Git's index and
unselected files are preserved. Each additional worktree can run `forklift project
setup` to get its own branch. Ordinary Git worktrees work without an IDE integration.

BB is an agentic IDE that manages projects and worktrees. Its optional
[setup/teardown hooks](docs/project.md#bb-worktree-hooks) call the same CLI.
The adapter does not automatically pause agents or supervise background applications.

For database-only branching, see the [CLI guide](docs/lifecycle.md). For a separate
owned source directory rather than a stable Git worktree, see [`forklift pair`](docs/paired.md).

## Limits

- Retention policies, pruning and garbage collection are **unsupported**. Old
  generations, checkpoints, source snapshots and failed attempts remain on disk.
  Stores are limited to 1,000 named branches/checkpoints. Plan for retained disk
  usage; there is no supported command to delete individual snapshots or branches.
- Captures require a clean database shutdown. Cooperating application commands and
  agents must be paused; live noninterrupting snapshots are unsupported.
- Selected source supports regular files/directories only: at most 10,000 entries,
  16 MiB per file and depth 64. No symlinks, hardlinks or nested exclusions.
- Checkpoint export requires a compatible PostgreSQL version, build and platform.
  Stores pin their PostgreSQL installation; automatic upgrades are unsupported.
- No cloud service, database merging, hosted auth/storage adapter or arbitrary
  host power-loss guarantee. This is a local development tool.

## Tests, design and evidence

Run the complete deterministic test suite with `python3 scripts/test.py`. Read
[CONTRIBUTING.md](CONTRIBUTING.md) for prerequisites, focused checks and the code map.
Forklift is [MIT licensed](LICENSE).

- [Architecture decision](docs/architecture-decision.md) and [checkpoint/cache format](docs/checkpoint-format.md).
- [20-branch demo](docs/demo.md), [100-branch measurements](docs/milestone-four.md), and [original feasibility benchmark](docs/feasibility.md).
- [Recorded parallel-agent experiment](results/2026-10-09-partial-payments-real/report.md): two worktrees implement partial payments differently, then one is rewound while the other's payment remains intact.
- [Project plan and milestone history](PROJECT_PLAN.md).
