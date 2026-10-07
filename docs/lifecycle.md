# using the milestone-two CLI

Forklift now manages local PostgreSQL 17 clusters on macOS ARM64. A branch is a
whole cluster with its own data directory and Unix socket. It is stopped until
you start it. There is no daemon, network listener, or object storage.

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
bound, not a tested concurrent-server capacity. No running-server cap exists yet.

## What each command does

| Command | Behavior |
| --- | --- |
| `init NAME` | Initializes an empty, stopped cluster with data checksums enabled. |
| `start NAME` | Starts the cluster, verifies identity and required durability settings. Already-running managed starts are idempotent. |
| `stop NAME` | Cleanly shuts down the identified server. Already-stopped branches remain stopped. |
| `connection NAME` | Prints a connection string; errors if the branch is stopped. |
| `list` | Reconciles recorded state with actual processes, then lists branches and checkpoints. |
| `checkpoint BRANCH --name SAVE` | Stops the source if running, creates a local physical snapshot, and resumes the source. |
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
PGDATA directories, external tablespaces, and extension installation are not
supported. **Only restore trusted dumps:** PostgreSQL archives can contain SQL
that executes with the database superuser's privileges. This is a local development
tool, not a sandbox for untrusted SQL or other users sharing your OS account.

APFS cloning is the default. The actual clone operation must succeed; there is
no silent copy fallback. Use `--copy-mode full` on checkpoint, branch or reset
for explicit buffered full copies. Command output and timing events identify the
selected mode; checkpoint records retain the mode used to create them. A full
copy is never presented as shared storage. Clone failures leave failed entries
and retained partial files; choose a new name for a retry.

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
while it prepares the replacement.

A checkpoint is selectable only after its complete stopped-cluster copy is
published as ready. Interrupted checkpoint/init/branch creation is marked failed,
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
Recovery happens on the next command, not automatically at host boot.

**Storage is retained conservatively.** Old reset/import generations, failed
partial generations and checkpoints remain on disk. No delete, garbage collection,
or recovery command for failed names is implemented. They are not automatically
reused. Do not sum APFS file block counts to claim unique physical space. For now,
use a new name after a failed creation; to discard an entire experimental store,
stop all of its servers first and remove that store manually. Never remove a store
with running or pending servers. Socket directories may remain under `/tmp/fl-*`;
they are private and are recreated after reboot when needed.

## Machine-readable output and tests

`--json` produces one JSON object. Success includes `ok`, total `elapsed_ms`, and
`result`; failures include `ok: false` and a stable `error.code` plus a message.
Usage errors exit 2; operational errors exit 1; success exits 0. Human help/version
remain ordinary text. Messages are limited to 8,000 characters and catalog files
to 4 MiB. Command results include stage timings for copying, source interruption,
server startup, identity/durability queries, and replacement downtime where
applicable. These are operation observations, not performance benchmark claims.

```sh
cargo fmt --check
cargo test --locked
cargo clippy --locked --all-targets --all-features -- -D warnings
cargo build --locked --features test-failpoints
python3 scripts/test_lifecycle.py
```

The integration test creates fresh owned stores under `benchmark-data/`, tests
real PostgreSQL, and stops its servers. It needs permission for PostgreSQL shared
memory. `test-failpoints` is an explicit opt-in build feature used to simulate
abrupt CLI exits at publication boundaries; ordinary builds contain no active
failure injection. Use `cargo build --locked --release` for the normal binary.
The test leaves raw evidence, a SUCCESS marker and all cluster files for inspection.
