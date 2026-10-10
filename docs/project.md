# Use Forklift with a Git project and BB worktrees

`forklift project` connects an existing Git worktree to an isolated local PostgreSQL
branch. It keeps the worktree path stable when restoring selected source files and
the database together. The adapter is Rust, with no application-specific code.

## Compatibility boundary

This is for development projects that can use Forklift's native local PostgreSQL 17
cluster. The tested host is macOS ARM64/APFS. PostgreSQL durability settings remain
enabled. The explicit full-copy backend is available via `--copy-mode full`.

The app must accept a PostgreSQL URL containing a Unix socket in the `host` query
parameter, use the `postgres` database as its development database, and have its
required extensions available in the same PostgreSQL installation. Forklift injects
that URL into your chosen environment variable. A driver that requires TCP, a
remote-only database, or an API client that talks to a hosted service needs further
adaptation. Auth, object storage, background workers, email and external API effects
are outside this checkpoint. This is not a substitute for those services.

## One-time setup

Build/install the binary somewhere on both your shell's and BB host daemon's PATH:

```sh
cargo install --path . --locked
```

Create a local seed cluster outside your project. Import a trusted development
`pg_dump` archive if you have one; import replaces the seed database. Checkpoint it:

```sh
forklift --home /absolute/path/to/store --pg-bin /opt/homebrew/opt/postgresql@17/bin init seed
# Optional, after preparing a trusted development archive:
forklift --home /absolute/path/to/store import seed --dump /absolute/path/dev.dump --yes
forklift --home /absolute/path/to/store checkpoint seed --name development
```

Alternatively initialize the empty seed using your app's migrations after setup,
then capture a baseline. Checkpoints include the whole cluster. Do not point this
at a production PostgreSQL data directory; Forklift creates and owns its store.

In your application repository create and commit `.forklift.json`:

```json
{
  "format": 1,
  "source": ["src", "migrations", "package.json", "package-lock.json"],
  "connection_env": "DATABASE_URL",
  "commands": {
    "prepare": ["npm", "ci"],
    "migrate": ["npm", "run", "migrate"],
    "verify": ["npm", "test"],
    "start": ["npm", "run", "dev"]
  }
}
```

These npm commands are examples: replace them with your project's real commands.
Argument arrays execute directly, without implicit shell parsing. If a command
needs shell syntax, explicitly use `["bash", "-c", "..."]`; these are trusted local
project commands. `PG_BIN` is also supplied for tools such as `psql`.

`source` is a list of **owned relative paths**, not glob patterns. Each selected
directory owns all its descendants, including selected dirty, ignored and untracked
files. Select individual files if siblings must be preserved. Missing paths are
allowed and are removed on restore when absent in the checkpoint. Paths cannot
overlap or contain Git metadata. `.forklift.json` and the two BB hooks are outside
the restore scope. Everything else outside the list stays untouched: `.git`, the
Git index, `.env.local`, dependencies and build output, unless you explicitly put
such files inside an owned directory. Do not select a directory containing secrets
or generated dependencies. There is no nested exclusion syntax in this version.

Limits inherited from paired capture: 10,000 entries, 16 MiB per file, depth 64,
UTF-8 paths, regular files and directories only, no symlinks/hardlinks/special files.
The adapter allows up to 128 nonoverlapping roots. Source configuration and commands
are pinned at setup; changing the contract requires a fresh worktree. Recovery
and database shutdown remain available if the configuration was edited.

Register the machine-local seed once, from the application repository:

```sh
forklift project configure --store /absolute/path/to/store --checkpoint development
forklift project setup
forklift project task prepare
forklift project task migrate  # only when your chosen seed needs migrations
forklift project task verify
forklift project capture baseline
```

`configure` records the store path and checkpoint in the repository's common Git
administrative directory. It does not import or validate the app against the seed;
`setup` checks the seed through the storage engine and `task verify` checks the app.
It applies to future bindings. Existing worktrees keep their database binding.

## Your normal coding loop

1. Run `forklift project capture before_change` before giving the agent a task.
2. Let the agent edit normally. Have it run database/application commands through
   `forklift project run -- COMMAND ...` or `forklift project task NAME`.
3. Inspect and test the result. Keep it with ordinary Git commits if you like it.
4. To rewind, **pause the BB agent and stop your app and every database writer**.
   Run `forklift project restore before_change`.
5. Reinstall or regenerate dependencies/build output as needed with
   `forklift project task prepare`, then `task verify`, then `task start`.

A foreground `task start` holds the worktree gate while the process runs. Other
managed commands (including migrations and tests), capture and restore refuse while
it holds the gate. Stop the foreground command first. Commands must remain synchronous; do not daemonize/background them.
The inherited gate remains held by cooperating child processes if the launcher
exits. BB agent edits made outside the wrapper are not automatically gated: the
explicit pause requirement is part of the contract.

Rewind needs one free running-server slot to prepare its replacement database.
The store defaults to three running clusters; a stopped application can still
leave its database running. If `restore` reports `running_limit`, no restore was
started and your files and database remain usable. After stopping the app and
pausing writers, free this worktree's slot and retry:

```sh
forklift project stop
forklift project restore before_change
```

You can instead park another branch or raise the limit with `forklift --home STORE
config --max-running N`. Admission failures do not require `project recover`.
For a failure after restore began, keep writers paused and run `forklift project
recover` before retrying or editing; this completes or rolls back the interrupted
restore according to its journal.

Each command receives the currently selected connection URL. No `.env` file is
written, and there is no persistent URL to silently become stale after rewind.
Do not cache/export the connection for use outside the managed command lifetime.
Application dotenv loaders must respect the injected variable. Git still handles
commits, branches and review; Forklift neither rewrites the index nor merges database
changes. If HEAD moved since capture, restored bytes appear as normal working-tree
changes relative to the current HEAD and index.

## BB worktree hooks

Commit these hooks in the app repo (templates are in `examples/project-hooks/`):

`.bb-env-setup.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail
forklift project setup
# Optional, once configured:
# forklift project task prepare
```

`.bb-env-teardown.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail
forklift project recover
forklift project stop
```

BB executes the tracked hooks with `env bash` from the new worktree. Its sanitized
environment supplies no BB project/worktree identity variables. Forklift derives
identity from Git instead. Register the seed before creating BB worktrees, and
ensure `forklift` is on the daemon's PATH. An absolute binary path in the hooks is
another option for a single-machine repo. The engine remembers the PostgreSQL
binary location in the store.

Each worktree gets a separate branch. Setup is idempotent after successful
publication. Interrupted setup attempts remain as retained staging directories and
stopped branches; rerunning setup prepares a new attempt. There is no automatic GC.

Stop foreground commands before archiving the last BB thread. Teardown recovers a
pending operation and parks the current database; it does not delete checkpoints.
A busy gate causes teardown to fail rather than stop another command's database.
BB can still remove the worktree after a failed hook: inspect the teardown result
and use `forklift --home STORE list` / `stop BRANCH` for retained running branches.

State lives under the common Git directory's `forklift-workspaces/IDENTITY`, outside
a linked worktree. A small `forklift-id.json` in that worktree's Git admin directory
identifies it; a setup lock protects identity creation. Removing/recreating a worktree gets a fresh identity; old snapshots
remain. This adds Forklift administrative files but leaves Git's HEAD, index and
history untouched. Moving an already-bound worktree is refused. Do not manually
operate on its underlying `pair` workspace while using the project adapter.

## Interrupted restore

`forklift project recover` is required after an interrupted restore. Managed commands
refuse to run until recovery finishes. Keep agents and unmanaged writers paused
through recovery too.

Restore first retains the selected pre-rewind source (including dirty/untracked
files), then journals the operation. The existing paired engine prepares a new DB
branch and chooses the old or new source/database pair. The adapter installs that
chosen source at the stable worktree path. If interrupted before pair publication,
recovery reinstalls the retained old source and keeps the old DB. After publication,
it finishes installing the requested source with the new DB. It verifies recorded
source hashes before touching files and rechecks installed bytes before unblocking.
Repeated recovery is safe. Source installation is not an atomic filesystem-wide
swap: unmanaged readers may see intermediate files while paused.

`forklift project inspect` shows the binding, pinned config and saved pairs. Its
pair inventory describes the last retained source mirror, not a live Git diff; use
Git to inspect current edits. Old source generations, attempted restores, abandoned
setup directories and database branches remain on disk. No retention policy or GC
is implemented, and no arbitrary host power-loss guarantee is claimed.
