# Use Forklift with a Git project

`forklift project` connects a Git worktree to an isolated local PostgreSQL branch
or a private native Supabase stack. It keeps the worktree path stable when restoring selected source files and
the database together. The adapter is Rust, with no application-specific code.
It works with ordinary Git worktrees. BB, an agentic IDE that manages worktrees,
can optionally run the same commands through setup/teardown hooks.

For a complete runnable example before adapting your application, start with the
[quickstart](../README.md#try-a-code-and-database-rewind).

Choose the [PostgreSQL setup](#one-time-setup) or
[native Supabase setup](#native-supabase-projects), then follow the shared
[coding loop](#your-normal-coding-loop). Optional [app hooks](#opt-in-managed-app-lifecycle)
drain an owned service group. Keep the [recovery instructions](#interrupted-restore)
available when experimenting.

## Compatibility boundary

This is for development projects that can use Forklift's native local PostgreSQL 17
cluster. The tested host is macOS ARM64/APFS. PostgreSQL durability settings remain
enabled. The explicit full-copy backend is available via `--copy-mode full`.

The app must accept a PostgreSQL connection URL. Unix sockets remain the default;
set `"tcp": true` for drivers that require TCP. Forklift then listens only on
`127.0.0.1` at a per-generation port, with a generated SCRAM password. Passwords
are stored in the private local store and passed to the child process; do not log
its connection environment. The connection uses the local development superuser
`forklift`; this is not an application authorization boundary. Other local users
cannot authenticate without a valid database-role password. Never expose this port
through a tunnel or share the database URL.
Project commands clear inherited libpq `PG*` connection settings before injecting
the selected connection URL and `PG_BIN`; separate `PGRST_*` service settings remain
available for a local PostgREST process.

Set `"database": "your_database"` to select an existing database in the seed.
The default remains `postgres`; Forklift does not automatically create or rename
application databases. Checkpoints include all databases in the cluster.
Supported extension versions and their installation fingerprints are checked at
capture/restore; see the [extension compatibility rules](architecture.md#compatibility-and-limits).

A Supabase JavaScript client uses HTTP, so changing `DATABASE_URL` alone does not
redirect it. Use the [native Supabase backend](#native-supabase-projects) for a local
stack, or provide a local PostgREST gateway and local client URL/key values for a
PostgreSQL-only workflow. PostgreSQL-only captures exclude Auth service state,
Storage files and external effects. Keep hosted credentials out of local experiments.

Run `forklift project doctor` before setup to check configuration and seed
availability, then again after setup to test the selected database and transport.
The JSON report contains `ready` and individual `ok`, `warning`, `pending` or
`blocked` checks. It never reads `.env` files or contacts a hosted database. After
setup it starts the local branch when needed (including configuring TCP) and
checks connectivity; it does not run migrations or application commands. A
warning about Supabase means the HTTP service still needs separate validation,
even if the PostgreSQL checks pass. The report itself does not contain passwords.

For projects with `supabase/config.toml`, see the optional [native Supabase backend](#native-supabase-projects).
Set `"backend": "postgres"` to explicitly keep this PostgreSQL-only workflow.

## One-time setup

Build/install the binary on your shell's PATH (and on the BB host daemon's PATH if
you use the optional hooks):

```sh
cargo install --path . --locked --bin forklift
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
  "database": "postgres",
  "tcp": false,
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
4. To rewind, **pause agents and unmanaged writers**. Stop foreground `run`/`task`
   commands; an app opted into the lifecycle hooks below is drained automatically.
   Run `forklift project restore before_change`.
5. Reinstall or regenerate dependencies/build output as needed with
   `forklift project task prepare`, then `task verify`, then `task start`.

A foreground `task start` holds the worktree gate while the process runs. Other
managed commands (including migrations and tests), capture and restore refuse while
it holds the gate. Stop the foreground command first. Commands must remain synchronous; do not daemonize/background them.
The inherited gate remains held by cooperating child processes if the launcher
exits. BB agent edits made outside the wrapper are not automatically gated: the
explicit pause requirement is part of the contract. Long-running services can
instead opt into the managed lifecycle below.

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
config --max-running N`. Without managed hooks, admission failures do not require `project recover`.
With managed hooks, the app has already been drained: run `project recover` to
clear its pending lifecycle record, then free a slot and retry.
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

## Opt-in managed app lifecycle

Implemented for both PostgreSQL and native Supabase: Forklift can coordinate one
application service group through synchronous **start, stop and status hooks**.
The group can contain an app server and workers that always start/stop together.
This is a trusted supervisor integration, not automatic process discovery. Existing
`commands.start` and foreground `project task start` keep their original behavior.

Add a `managed` block to `.forklift.json` **before setup** (the contract is pinned):

```json
{
  "format": 1,
  "source": ["src", "package.json"],
  "commands": { "verify": ["npm", "test"] },
  "managed": {
    "start": ["/absolute/path/to/app-supervisor", "start"],
    "stop": ["/absolute/path/to/app-supervisor", "stop"],
    "status": ["/absolute/path/to/app-supervisor", "status"],
    "timeout_seconds": 30
  }
}
```

The executable in this example is an application-provided supervisor, not a
Forklift-supplied binary. Keep the supervisor and its control scripts **outside
restored source** so stop/status still work after a partially installed restore.
Each array is an executable plus arguments; there is no implicit shell. Hooks run
in the worktree directory. Use absolute executable paths for stable resolution.
The hook protocol is:

- `start`: start only this worktree's group and wait for readiness, then exit 0.
  It receives the current database connection and, for native Supabase, the same
  local credentials as `project run`. Every restart gets freshly selected values.
  Do not cache old endpoints or load hosted credentials over the injected values.
- `stop`: stop admission of new work, drain in-flight requests/jobs, finish or
  roll back transactions, flush source writes, disable automatic restarts, and
  **wait for every owned writer to exit**, then exit 0. It must be idempotent and
  safe after a partially failed start. It must not stop another worktree's group.
- `status`: read-only; exit 0 only when the complete group is running, or 3 only
  when all its writers are stopped and no delayed restart can occur. Any other
  exit means unknown/partial/unhealthy and blocks the operation. Independent
  services with different running subsets require their own external lifecycle;
  Forklift does not infer a per-process resume set within the group.

All hooks receive `FORKLIFT_MANAGED_STATE`, the private, worktree-specific Forklift
state directory, and `FORKLIFT_GATE_FD`, the inherited lock descriptor. Keep
supervisor control state in a uniquely named subdirectory there, not in selected
source. Stop/status receive the caller's ordinary environment, **not freshly
started database services or connection credentials**; they must work while the
backend is stopped or recovery is pending. They should use their own authenticated
control channel and verified service identity, not signal arbitrary saved PIDs.

The synchronous hook and all its short-lived helpers must retain the gate until
they finish. Only the deliberately detached application/supervisor process must
close it, along with standard input/output/error (for example Python `Popen` with
`close_fds=True`, `stdin=DEVNULL`, and private log handles). The hook must wait for
that process's readiness before returning. A detached app that keeps the gate
will cause subsequent commands to refuse; Forklift will not break the lock.
Do not close the gate in the hook itself or leave delayed background hook work.
Hooks receive no stdin; output is suppressed to avoid leaking connection secrets.
Provide private supervisor logs when diagnostics are needed.

```sh
forklift project app-start
forklift project capture before_change  # drain, save, resume
forklift project restore before_change  # drain, restore, resume with fresh URLs
forklift project app-stop               # drain only; leave DB/services available
forklift project task verify
forklift project app-start
forklift project stop                   # drain app, then park DB/services
```

`app-start` is idempotent for an owned, healthy running group. Forklift records
ownership before start, including partial starts. It never adopts an externally
started group: a running status without recorded ownership refuses capture,
restore and recovery until the external owner stops it. A previously owned group
that has fully exited is not restarted by capture/restore. Capturing while stopped
also stays stopped. `inspect` shows the recorded `managed.phase` and `owned` flag;
it is not a live health check and does not run status hooks.

The existing per-worktree gate remains held from status/stop through source and
backend work to restart. Concurrent commands refuse. While the group is running,
ordinary `run`/`task`, forget and prune refuse; use `app-stop` first. Other worktrees
have independent gates and lifecycle records. The store's existing short-lived
shared catalog lock and running-server limit still apply.

### Failures and recovery

A synced `managed.json` record marks the operation pending **before any hook**.
The existing source/database restore journals remain responsible for choosing and
installing a consistent pair. Pending app intent is separate from a capture and
is never rewound along with source.

| Outcome | Implemented behavior |
| --- | --- |
| Stop fails, status is unknown, or stop returns while status is running | No capture/restore begins; normal managed commands block until recovery. |
| Capture/restore fails, even before its own journal is written | No automatic restart; app intent stays pending. Recover before retrying. |
| Start/restart fails | The successful capture/restore is retained; potentially partial app startup is pending. No rollback or automatic retry. |
| Hook exceeds `timeout_seconds` (1–3600 seconds per hook) | Return an error without killing/suspending it. The hook retains the gate until exit; after that the pending record still blocks work. |
| Forklift is interrupted | Live synchronous hooks retain the inherited gate. Pending intent remains after they exit. There is no stale-lock removal. |
| `project recover` succeeds | Stop/confirm quiescence first, recover the backend/source, then mark the app stopped. It never automatically restarts the app. |

After a failure or interruption, keep unmanaged writers paused, allow any live
hook to finish, repair the supervisor if needed, and run:

```sh
forklift project recover
forklift project task verify
forklift project app-start
```

Recovery retries the stop hook for owned intent before querying status, including
partial startups whose status is unknown; only a stopped status after the hook
authorizes backend recovery.
Repeated recovery is safe. If a hook never exits, exclusion stays held; arrange
its graceful completion through the supervisor. Forklift does not kill it or
silently declare it safe. A malformed journal also fails closed. Do not delete
lifecycle records to bypass recovery. `app-stop` and `stop` require recovery first
when intent is pending. Recovery/stop use the pinned hook contract even if the
configuration file was edited; normal startup still requires matching config.

The hooks' truthful quiescence/ownership report is a cooperation boundary. Forklift
cannot verify every OS writer, pause arbitrary coding agents, rewind external
side effects, or restore in-memory sessions. Pause all agents and unmanaged source
and database writers yourself, including browser clients of native Supabase.
Forklift does not send SIGSTOP/SIGKILL to applications, guess PIDs, or relax
durability. A hook that
lies about stopped writers violates the contract. Process-interruption tests do
not establish arbitrary host power-loss safety.

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

Stop foreground commands before archiving the last BB thread. Managed hooks let
teardown drain the owned app group. Teardown recovers a
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
Git to inspect current edits. `forklift project forget NAME` previews removing a
saved capture, and `forklift project prune` previews reclaiming obsolete source
copies, stopped owned branches and unreachable checkpoint objects. Add `--yes` to
apply either operation. Current source/database state, remaining captures, borrowed
checkpoints and legacy resources without ownership tracking remain protected.

Configured setup seeds stay pinned even before the first worktree is created.
Reconfigure to replace that seed, or use `forklift project unconfigure --yes` to
remove the shared setup configuration and release its pins; existing worktree
bindings remain usable. See [discarding experiments](cli.md#discarding-experiments)
for previews, interruption recovery and ownership rules. Abandoned setup directories
and unrecognized files remain retained. Automatic retention policies are not
implemented. No arbitrary host power-loss guarantee is claimed.

## Native Supabase projects

Forklift can manage an optional, private **local Supabase** stack for a Git
worktree. The ordinary PostgreSQL backend remains available and requires no
Supabase CLI, credentials, or service downloads.

The native Supabase backend supports macOS ARM64/APFS, **Supabase CLI 2.120.0**,
and its native PostgreSQL 17 runtime. It runs PostgreSQL, the REST API, Auth,
Storage, and Realtime. Docker/Podman stacks and existing Supabase-managed stacks
are not adopted. Forklift creates a separate owned stack for each worktree.

### Setup

Install Forklift, the pinned [Supabase CLI release](https://github.com/supabase/cli/releases/tag/v2.120.0),
and the PostgreSQL 17 utilities required by the normal Forklift installation.
The Supabase native distribution supplies the database server and extensions;
Forklift also uses `pg_controldata` and `pg_checksums` from your PostgreSQL 17
installation to verify saved database files. The first start downloads Supabase's
native service artifacts. Subsequent starts use the private runtime cache.

In a Git project with `supabase/config.toml`:

```sh
forklift project setup
forklift project doctor
forklift project run -- npm run dev
```

Set `FORKLIFT_SUPABASE_BIN=/absolute/path/to/supabase` if the pinned CLI is not on
PATH. Its location is saved in the binding. Use `--pg-bin PATH` before `project`
if your PostgreSQL 17 utilities are outside the default Homebrew installation.

Setup detects the Supabase configuration and creates `.forklift.json` if it is
missing. It selects conventional application paths plus the existing
`supabase/config.toml`, migrations, seed.sql, and functions directories. Review
that source list and commit the configuration. The normal source limits apply;
large assets, links and nested exclusions need an explicitly narrower selection.
Dotenv files, dependencies, and the original `supabase/.temp` are not copied.

The input config must specify PostgreSQL 17. A project declaring PostgreSQL 15
must first prepare and validate a PostgreSQL 17 local configuration; Forklift
does not silently upgrade it. Hosted-only projects need local configuration and
migrations first. Setup applies local migrations/seeds to the newly created stack;
it does not pull production data, credentials, or remote schema changes.

Forklift copies the local service configuration into private state, removes fixed
port assignments so worktrees receive separate endpoints, and forces durability
settings on. It enables database checksums offline before allowing captures.
The original `supabase/config.toml` is unchanged.

### Backend and environment selection

Use `"backend": "postgres"` or `"backend": "supabase"` in `.forklift.json` to
override detection. Existing bound PostgreSQL worktrees keep their backend.
Changing backends requires a fresh worktree; a saved binding cannot be reused for
a different backend or copied to another location.

An explicit Supabase configuration can look like:

```json
{
  "format": 1,
  "backend": "supabase",
  "source": ["src", "package.json", "supabase/config.toml", "supabase/migrations", "supabase/seed.sql"],
  "connection_env": "DATABASE_URL",
  "supabase_env": {
    "API_URL": "VITE_SUPABASE_URL",
    "PUBLISHABLE_KEY": "VITE_SUPABASE_PUBLISHABLE_KEY"
  },
  "commands": { "start": ["npm", "run", "dev"] }
}
```

By default, managed commands receive `DATABASE_URL`, `SUPABASE_URL`,
`SUPABASE_ANON_KEY`, `SUPABASE_PUBLISHABLE_KEY`, and the corresponding
`NEXT_PUBLIC_SUPABASE_*` URL/client-key variables. Server-only keys are passed as
`SUPABASE_SERVICE_ROLE_KEY` and `SUPABASE_SECRET_KEY`. Custom `supabase_env` maps
Supabase output names (`API_URL`, `DB_URL`, `ANON_KEY`, `PUBLISHABLE_KEY`,
`SERVICE_ROLE_KEY`, `SECRET_KEY`) to your application's names. Secret keys and
database credentials cannot be mapped to recognized public-client variable
prefixes. Never log the child
process environment or expose server keys to a frontend.

`project run` and `project task` start the local services and inject these values.
They hold the workspace gate while the app runs. Stop the managed app command
before capturing or restoring; Forklift refuses to interrupt a command holding
that gate. Alternatively, configure the [opt-in managed lifecycle](#opt-in-managed-app-lifecycle)
before setup and use `project app-start`. Capture/restore then drain the owned app
group before stopping native services, and restart it with fresh local URLs/keys
only after success. A stopped app stays stopped. `project app-stop` drains just
the app; `project stop` drains it before stopping the native stack.
Pause unmanaged writers and agents too. Raw CLI errors are kept in a
private runtime log because they may contain local credentials.

### Save and rewind

```sh
forklift project capture baseline
# Change source, run migrations, change rows or upload local files.
forklift project restore baseline
forklift project run -- npm run dev
forklift project stop
```

Capture cleanly stops all managed services, verifies the stopped database and
page checksums, and saves matching selected source, database state, uploaded
Storage bytes, and normalized runtime configuration. Auth records, database
roles, RLS policies, functions, and migration history are part of that database.
Services restart after successful capture or restore. Realtime connections must
reconnect after restart; browser memory and cookies are not restored.

Snapshots use stopped filesystem copies/APFS clones. They do not use the
ordinary PostgreSQL backend's packed objects, deduplication, or exports. File
hashes and an exact runtime fingerprint are checked before restore. A runtime or
CLI upgrade can make existing captures incompatible; automatic upgrades and
cross-runtime snapshot migration are unsupported.

An interrupted restore leaves a journal and blocks normal managed commands:

```sh
forklift project recover
```

Recovery completes the selected restore, including source and Storage files,
before restarting native services. If managed app hooks are configured, recovery
first confirms app quiescence and leaves the app **stopped** after recovery; use
`project app-start` explicitly. Hook failures/timeouts keep a separate lifecycle
journal pending, even if the backend restore succeeded. See the shared
[failure contract](#failures-and-recovery). Previous dirty source and local state are retained
until explicit pruning. This is tested process-interruption recovery, not a
claim of arbitrary host power-loss recovery.

`project forget NAME` previews removing a capture; `project prune` previews
reclaiming forgotten snapshots, pre-restore copies, and incomplete staging data.
Add `--yes` to apply. Current runtime data, artifact caches, and named captures
remain protected. Stop your stack when finished to free resources.

### Scope and verification

Studio, Edge Functions execution, mail, analytics, and connection pooling are
excluded from this backend. Function source can be saved but is not
executed. OAuth providers, external SMTP, third-party APIs, and already-delivered
external effects are not rewound. This is a local native stack integration, not a
hosted Supabase backup or security audit.
