# using the Forklift CLI

Forklift now manages local PostgreSQL 17 clusters on macOS ARM64. A branch is a
whole cluster with its own data directory and Unix socket. It is stopped until
you start it. There is no daemon. Unix sockets are the default; project configuration
can opt into SCRAM-authenticated TCP on `127.0.0.1` for PostgreSQL drivers that need
it. See [project connections](project.md#compatibility-boundary). Saved checkpoints use local object
storage; see the [format and portability contract](checkpoint-format.md).

For a single-command, measured 20-branch walkthrough, see [the complete demo](demo.md).

## Build and try it

Install native PostgreSQL 17 and Rust 1.95.0, then run from this repository:

```sh
export PATH="$HOME/.cargo/bin:/opt/homebrew/opt/postgresql@17/bin:$PATH"
cargo build --locked --release
export PATH="$PWD/target/release:$PATH"

forklift init demo
forklift start demo
psql "$(forklift connection demo)" -v ON_ERROR_STOP=1 -v rows=1000 -f fixtures/seed.sql
forklift checkpoint demo --name seed
forklift branch demo --from seed --name experiment-a
forklift branch demo --from seed --name experiment-b
forklift start experiment-a
forklift start experiment-b
psql "$(forklift connection experiment-a)" -v ON_ERROR_STOP=1 -f fixtures/edit.sql
psql "$(forklift connection experiment-b)" -v ON_ERROR_STOP=1 -f fixtures/conflict.sql
forklift list
forklift reset experiment-a --to seed --yes
forklift stop experiment-a
forklift stop experiment-b
forklift stop demo
```

Reset removes A's migration and edits; B and the saved seed remain unchanged.
Clients connected to a branch being stopped or reset are disconnected. Re-run
`connection` after reset: the replacement uses a new socket. Connection strings
use normal libpq syntax, so ordinary Postgres clients can use them.

To move a checkpoint into a fresh store:

```sh
forklift export seed --output seed.forklift
forklift --home .forklift-restored restore seed.forklift --name restored
forklift --home .forklift-restored start restored
psql "$(forklift --home .forklift-restored connection restored)" -c 'SELECT count(*) FROM accounts'
forklift --home .forklift-restored stop restored
forklift stats
```

`seed.forklift` is a self-contained directory and must not already exist. The
restored branch needs neither the original source cluster nor its object repository.
Use a compatible PostgreSQL installation; current checks require the same version,
build configuration and platform. Export only new object checkpoints. To convert
a legacy physical checkpoint, branch from it and save a new checkpoint.

The default store is `.forklift` under the current directory. Use `--home PATH`
for another store. Use the same home on subsequent calls; the catalog pins the
PostgreSQL binary directory and platform at creation. `--pg-bin PATH` overrides
the initial Homebrew default, but cannot silently change an existing store's
PostgreSQL installation. If Homebrew removes that pinned installation, restore a
compatible installation at its recorded location before managing that store;
an explicit upgrade workflow is not implemented yet.

Names accept 1–48 lowercase letters, digits, underscores and hyphens. Existing
names are never overwritten by init, branch or checkpoint, including failed
entries. There are at most 1,000 named catalog entries. This is a metadata/output
bound, not a tested concurrent-server capacity. The default running limit is three
clusters per store, including temporary import servers.

## What each command does

| Command | Behavior |
| --- | --- |
| `init NAME` | Initializes an empty, stopped cluster with data checksums enabled. |
| `start NAME` | Starts the cluster, verifies identity and required durability settings. Already-running managed starts are idempotent. |
| `stop NAME` | Cleanly shuts down the identified server. Already-stopped branches remain stopped. |
| `connection NAME` | Prints a connection string; errors if the branch is stopped. |
| `config [--max-running N]` | Shows or sets the per-store running limit (1–100, default 3); stop servers before lowering below the active count. |
| `list` | Reconciles recorded state with actual processes, then lists branches and checkpoints. |
| `checkpoint BRANCH --name SAVE` | Stops the source if running, writes a checksummed object checkpoint, and resumes the source. |
| `export SAVE --output DIR` | Exports a complete checkpoint manifest and reachable packs into a new directory. |
| `restore DIR --name NAME` | Materializes a verified new stopped branch; can initialize a fresh store. |
| `delete-branch NAME [--yes]` | Preview removal of a stopped, independently managed branch; coordinator resources require pair/project prune. |
| `delete-checkpoint NAME [--yes]` | Preview removal of a checkpoint with no branch references. |
| `gc [--yes]` | Preview removal of unreachable object manifests and packs. |
| `cleanup [--yes]` | Previews unreferenced generations and disposable cache; `--yes` permanently removes the listed files. Does not reconcile pending operations. |
| `stats` | Counts all retained generation/object/cache files, including incomplete work; allocated bytes are not unique APFS space. |
| `branch [SOURCE] --from SAVE --name NAME` | Creates a stopped cluster from a ready checkpoint. Optional SOURCE checks ancestry. |
| `reset NAME --to SAVE --yes` | Prepares a replacement from SAVE, stops the target, switches it, and preserves its previous running/stopped state. |
| `import NAME --dump FILE --yes` | Replaces NAME with a fresh cluster restored from a trusted custom/tar pg_dump archive. Preserves running/stopped state. |

An import is deliberately explicit about replacement, even for an empty branch.
For example, while `demo` is running:

```sh
mkdir -p benchmark-data
pg_dump "$(forklift connection demo)" -Fc -f benchmark-data/seed.dump
forklift init imported
forklift import imported --dump benchmark-data/seed.dump --yes
forklift start imported
```

Import targets the `postgres` database and uses a single restore transaction.
Roles and ownership/privileges are not imported. Plain SQL scripts, external live
PGDATA directories and external tablespaces are unsupported. Checkpoints support
`plpgsql` 1.0, `citext` 1.6, `hstore` 1.8, `uuid-ossp` 1.1, `pg_trgm` 1.6 and
`vector` 0.8.0 across all connectable databases. Install pgvector separately when
your application needs it. The bundled extension control files, SQL scripts and libraries must
match when materializing a checkpoint; see the
[extension compatibility rules](checkpoint-format.md#compatibility-and-limits).
Importing other extensions can succeed if PostgreSQL has them installed, but
checkpointing that cluster will fail; this also prevents paired/project capture. **Only restore trusted dumps:** PostgreSQL archives can contain SQL
that executes with the database superuser's privileges. This is a local development
tool, not a sandbox for untrusted SQL or other users sharing your OS account.

New checkpoints split stopped cluster files into hashed chunks, reuse saved content,
and compress new chunks into immutable packs. The first branch/reset reconstructs
and verifies a private, stopped cache. Later operations verify its contents and
clone it using APFS. Each branch can write independently; the cache never runs.
`--copy-mode full` explicitly copies cached files instead. Neither path silently
falls back. `--no-cache` reconstructs directly from objects for diagnosis/comparison.
Portable restore always reconstructs from the exported bundle.

`list` labels stopped branches **parked**: saved files with no running server.
The JSON `state` remains `stopped`, with a separate `activity: "parked"` field.
Activation is explicit; connecting does not start a branch. `active_clusters` and
`max_running` report the count and limit. PostgreSQL workers are additional
processes within each cluster; this is not a cap on every OS process or on other
stores. `initdb` also uses a short-lived bootstrap process.

At the cap, starting another branch or importing into a parked branch returns
`running_limit`. An import into a running branch can reuse its slot: Forklift
stops that target before preparing the import, so its interruption includes the
whole import. On failure, reconciliation resumes the old selected generation.
A checkpoint of a parked branch briefly needs a slot to inspect its extensions.
Use `forklift config --max-running 5` to change the limit. Larger settings are
allowed, but only three simultaneous servers were exercised in the M4 benchmark.

## Saved state and recovery

The catalog is a small versioned JSON file. A command holds an exclusive file
lock throughout its operation; a competing command gets `locked` and can retry.
Catalog updates write a fresh file, sync it, rename it, then sync the directory.
Generation ownership markers and strict relative identifiers keep operations
inside the store. Symlinks, hard-linked cluster files and special files are
rejected. A pre-existing directory without a valid store is not adopted.

Each change that replaces data creates a new **generation**: a separate cluster
directory. Reset never copies over the current data. It prepares and syncs a
replacement, stops the old server, then atomically switches the catalog's pointer.
If the command dies before the switch, the old generation stays selected. If it
dies after the switch, the replacement stays selected. The next command finishes
reconciliation and resumes the selected branch when it was previously running.
Import follows the same publication rule and can leave the old server running
while it prepares the replacement if a spare running slot is available.

A checkpoint is selectable only after all packs and its manifest are durable
and the catalog publishes it as ready. Interrupted checkpoint/init/branch creation is marked failed,
never silently promoted to ready. Interrupted imports stop their pending server
and keep the previous selected generation. Failed entries cannot be started.

Process checks combine the PID, OS start information, PostgreSQL start time,
pidfile directory/socket and a SQL check of the connected database directory.
Forklift refuses a mismatched identity instead of signaling a possibly unrelated
process. After an external clean or immediate shutdown, the next command observes
the stopped process; start allows PostgreSQL to perform its own WAL recovery.
A partially written pidfile or unresponsive server produces an error; retry after
startup finishes, or inspect it manually. It does not guess which process to kill.
Do not manually move/edit a store or start its clusters with custom configurations.

`list` performs reconciliation, so it may resume a branch whose earlier operation
was interrupted. This is intentional; it is not a purely read-only command.
Recovery happens on the next command, not automatically at host boot. If the
limit is full, a restart stays pending (`resume_pending`) and a
`resume_deferred_capacity` event explains why. You can still stop branches or
raise the limit. Explicitly stopping a branch cancels its pending restart.

**Cleanup is explicit and conservative.** Run `forklift cleanup` to preview
eligible paths and apparent bytes, then `forklift cleanup --yes` to delete them.
It reclaims old reset/import generations that no catalog entry references, plus
published, quarantined and incomplete materialized caches. Cache entries rebuild
from immutable checkpoint objects when needed. Preview does not reconcile pending
operations or delete files (opening a legacy store can still upgrade its catalog).
Cleanup holds the store lock and refuses unsafe ownership, links, special files
and any candidate containing a pidfile, even a stale one. Resolve that condition
before retrying; cleanup never signals processes. Validation covers the entire
plan before deletion begins.

Selected branches, pending replacements, all named checkpoints (including legacy
physical checkpoints), and failed named creations remain protected. Pair/project
restore generations retained as named branches also remain protected. Objects,
orphan packs, unrecognized temporary files, external exports and socket directories
are outside this cleanup scope. Named deletion and object garbage collection are separate explicit commands
described below. Run `list` to reconcile interrupted lifecycle work;
then preview cleanup again to include newly unreferenced generations.

An applied cleanup writes a durable `cleanup.json` journal before removing files.
If interrupted, rerun `cleanup --yes` to finish the recorded plan; a preview reports
its remaining files with `resuming: true`. Partially removed trees are safe to retry.
The catalog is checked again so a currently referenced generation is never deleted.
After finishing a resumed plan, another preview can identify newer candidates.
Process-exit tests exercise this protocol; they do not establish arbitrary host
power-loss safety.

Use `stats` to include retained work. Apparent bytes and file block counts do not
measure unique physical APFS space reclaimed because clones can share blocks.
External exports and hidden export staging directories need separate accounting.
To discard an entire experimental store, stop all of its servers first and remove
that store manually. Never remove a store with running or pending servers.
Socket directories may remain under `/tmp/fl-*`; they are private and recreated
after reboot when needed.

## Machine-readable output and tests

`--json` produces one JSON object. Success includes `ok`, total `elapsed_ms`, and
`result`; failures include `ok: false` and a stable `error.code` plus a message.
Usage errors exit 2; operational errors exit 1; success exits 0. Human help/version
remain ordinary text. Messages are limited to 8,000 characters and catalog files
to 4 MiB. Command results include stage timings for copying, source interruption,
server startup, identity/durability queries, and replacement downtime where
applicable. These are operation observations, not performance benchmark claims.

Run all deterministic checks with `python3 scripts/test.py`. See
[contributing](../CONTRIBUTING.md) for prerequisites, focused test commands,
historical compatibility checks and separate test/release build directories.

The integration test creates fresh owned stores under `benchmark-data/`, tests
real PostgreSQL, and stops its servers. It needs permission for PostgreSQL shared
memory. `test-failpoints` is an explicit opt-in build feature used to simulate
abrupt CLI exits at publication boundaries; ordinary builds contain no active
failure injection. Use `cargo build --locked --release` for the normal binary.
The test leaves raw evidence, a SUCCESS marker and all cluster files for inspection.

## Application code and database rewind

The native `forklift pair` commands coordinate owned, small application workspaces.
Milestone six established the protocol in Python; milestone seven moved it into Rust. It saves source files with a database checkpoint,
coordinates synchronous application writes, and restores a coherent pair while
retaining prior work. See [the contract, recovery rules and commands](paired.md)
and [the authentic agent experiment](../results/2026-10-08-paired/report.md).

## Explicit retention

For an ordinary database experiment, stop the branch first, then preview each step:

```sh
forklift delete-branch experiment-a
forklift delete-branch experiment-a --yes
forklift delete-checkpoint old_snapshot
forklift delete-checkpoint old_snapshot --yes
forklift cleanup
forklift cleanup --yes
forklift gc
forklift gc --yes
```

Deletion removes a name from the catalog; cleanup removes its unreferenced database
files. GC traces all remaining checkpoint manifests and any locally stored import
origins. A pack shared by two checkpoints stays until neither needs it. Exports
outside the store remain independent. Deleting a referenced checkpoint fails;
remove the referring branches first. Configured project seeds are also pinned;
reconfigure or use `project unconfigure --yes` to release that setup reference. Running branches and pending replacements
cannot be deleted. Pair/project resources must be removed through their owning
workspace; see [paired retention](paired.md#discarding-experiments).

Names created before retention tracking remain protected: older workspaces may
still reference them without a store-visible ownership record. There is no unsafe
force override. Catalog format 5 records new resource provenance and coordinator
ownership; opening an older store upgrades it conservatively. Older binaries
reject format 5, preventing them from publishing references that retention cannot
track. Keep a backup before upgrading if you need the older binary.

Catalog deletion is atomic. An interrupted deletion either leaves the name or
removes it; rerun cleanup and GC to reclaim remaining files. GC deletes dead
manifests durably before their packs, so it can safely replan after interruption.
All remaining checkpoint manifests are validated before GC removes anything.
Automatic age/count-based retention remains unimplemented. Apparent bytes are file
sizes, not a promise of unique physical space freed on APFS.
