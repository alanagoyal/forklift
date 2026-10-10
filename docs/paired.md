# paired application workspaces

The native `forklift pair` commands capture and restore selected application source
files with a matching PostgreSQL checkpoint. The coordinator is implemented in
[`src/paired.rs`](../src/paired.rs), using the existing Rust lifecycle and storage
modules directly. Python remains only in the fixture, demo runner and independent
integration tests. See [the Rust integration report](history/milestone-seven.md).

## Implemented contract

A workspace is newly created and owns its source directories. Initialization
copies an explicitly selected small source tree and forks an existing database
checkpoint into a private branch. It never runs Git reset/clean on the user's
checkout. All files in that source directory are captured, including dirty,
untracked and ignored files; there is no implicit Git exclusion policy. Do not
put secrets or dependencies there unless you intend to retain them in snapshots.
Git metadata, symlinks, hardlinked files and special files are rejected. Limits
are 10,000 entries, 16 MiB per file, 64 directory levels, and 16 MiB when
reading a metadata file. Source paths must be valid UTF-8. Empty directories and file permission bits
are preserved; ownership, timestamps, ACLs and extended attributes are not part
of the restore contract. The repository's unrelated `learning-site/` is excluded
by selecting only `fixtures/invoice-app/` as the initial source.

Every controlled application/agent invocation uses `forklift pair WORKSPACE run --
COMMAND`. It acquires an exclusive advisory gate, resolves the current source and
branch together, starts that branch, supplies `DATABASE_URL` and `PG_BIN`, and
holds the gate until the synchronous child exits. The child inherits the gate
file descriptor so a killed coordinator cannot immediately admit a capture while
that child still runs. No detached children, independent database clients,
external editors or background workers may write during capture/restore.
This is cooperative coordination, not a sandbox or a transparent proxy for
arbitrary applications. Agents have access to trusted local code and SQL.

Capture takes the same gate. For this fixture there are no persistent application
processes, so the explicit quiescence boundary is the prior command's exit plus
gate acquisition. Source bytes and modes are hashed and copied into a saved
directory, verified, and synchronized. The Rust engine then captures a stopped
PostgreSQL checkpoint and resumes the source as appropriate. A durable pair
manifest references the source inventory and database checkpoint only after both
exist. The database checkpoint name is unique and immutable in the existing
catalog. The saved source is checked against its hashes before every restore.
Source snapshots are local files, not a portable paired export or a Git commit.

Restore deliberately preserves the old source directory and database branch:

1. Write and sync a pending journal containing old and intended new pair references.
2. Verify saved source, copy it into a fresh working source directory and sync it.
3. Use the Rust branch command to prepare a fresh database from the saved checkpoint.
4. Start and validate that database using the existing identity/durability checks.
5. Durably replace `current.json`, selecting the new source directory and branch.
6. Stop the old branch and clear the journal durably.

This consumes a spare running-cluster slot while preparing a replacement. At the
limit, restore refuses before writing its journal or copying source/database files;
the current pair remains usable and no recovery is needed. Run `forklift pair
WORKSPACE stop` to park its database, stop another branch, or raise the store's
`config --max-running` limit, then retry. Stopping an application command alone
does not park its database. The store lock is held from admission through pair
publication so another managed command cannot take the reserved capacity.
Interrupted operations after admission still require recovery as described below.
Repeated restores retain old source, branches, checkpoints and caches.
Store-level `forklift --home STORE cleanup` previews rebuildable caches and
unreferenced database generations; add `--yes` to apply. It preserves every
named branch, checkpoint and source snapshot, including old paired branches.
See [cleanup and recovery](lifecycle.md#saved-state-and-recovery).

Checkpoint capture supports `plpgsql` 1.0 and the bundled `citext` 1.6, `hstore` 1.8
and `uuid-ossp` 1.1 extensions with matching installation files. Other extensions
remain unsupported. See the [project compatibility boundary](project.md#compatibility-boundary)
before importing an application database.

## Recovery semantics

There is **no filesystem transaction encompassing code and PostgreSQL**. They are
prepared independently; a small durable reference selects an already prepared
pair. Readers must use the coordinator to respect this boundary. A pending journal
blocks `run`, capture, restore, inspect and stop until explicit `recover` succeeds.
The Rust catalog reconciles its own interrupted operations before pair recovery.

| Interruption | Recovery selection |
| --- | --- |
| Capture before pair manifest publication | Original current pair; incomplete saved files/checkpoints retained as orphans |
| Capture after manifest publication | Original current pair; the complete new checkpoint remains selectable |
| Restore during code/database preparation or database startup | Old current pair; prepared replacement server stopped if it exists |
| Restore after `current.json` publication | New coherent pair; old server stopped |
| Failure during recovery | Journal remains; retry recovery after resolving the underlying issue |

Recovery is idempotent and occurs on request, not at host boot. A missing/corrupt
source snapshot fails before changing the current pair. Metadata and the workspace
are trusted local state; manual catalog/path tampering is unsupported. Source
checksums detect accidental corruption, not malicious edits by the same OS user.
A failed agent command may have committed partial work: explicitly restore a saved
pair to undo it. The command gate alone does not roll back application transactions.

Covered: selected source files, the entire owned PostgreSQL cluster's schema and
data, and the current pair reference. Excluded: sent emails, external APIs, queues
outside that database, arbitrary filesystem effects, running process memory,
open editor buffers, dependency installations, Git history/index, other branches,
and the separate Basecase and greplcia projects. PostgreSQL fsync, full-page WAL,
synchronous commits and data checksums stay enabled.

## Reproduce the real experiment

Use macOS/APFS with the repository's pinned Rust and PostgreSQL 17 installation.
PostgreSQL needs shared-memory and Unix-socket access. The agent requires a working
Codex CLI login; the provider call may use a network service even though all
Forklift storage and database operations are local. Inspect `codex exec --help`
when using a different CLI version. The runner records its exact argv and prompt,
allows local socket networking in the agent sandbox, and bounds execution to ten
minutes. A timeout kills the invocation's process group; inspect state afterward.

```sh
export PATH="$HOME/.cargo/bin:/opt/homebrew/opt/postgresql@17/bin:$PATH"
cargo build --locked --release
python3 scripts/demo_paired.py prepare benchmark-data/my-pair results/my-pair
python3 scripts/demo_paired.py agent benchmark-data/my-pair results/my-pair
python3 scripts/demo_paired.py finish benchmark-data/my-pair results/my-pair
```

Both output directories must be new. `prepare` seeds three invoices, saves the
baseline and leaves the owned application database running for the agent.
`agent` asks the actual provider to implement and apply invoice archiving;
`finish` independently observes the changes, saves the modified pair, restores
the baseline, asserts exact source/schema/row/application equality, checks source
and sibling isolation, and stops every server in this dedicated demo store.
Model output is nondeterministic: a zero agent exit code alone is not acceptance.
The finish assertions and transcript review establish what actually happened.

Inspect or run the restored application again:

```sh
target/release/forklift pair benchmark-data/my-pair/workspace inspect
target/release/forklift pair benchmark-data/my-pair/workspace run -- python3 app.py
target/release/forklift pair benchmark-data/my-pair/workspace stop
```

After an interruption, first run `forklift pair WORKSPACE recover`. If an experiment
fails before `finish`, use that recovery command and `forklift pair WORKSPACE stop` to
park the selected app. For the dedicated demo store, inspect `forklift --home
benchmark-data/my-pair/store list` and stop any other running owned branches before
removing files. A failed initializer can leave an owned partial store; inspect it
with the Rust CLI rather than retrying into existing output directories.

## Validation and planned work

Run `python3 scripts/test.py` for the complete deterministic suite, including paired
recovery and historical compatibility. See [contributing](../CONTRIBUTING.md) for
prerequisites and focused checks. No authenticated coding agent is needed for tests.

The paired suite uses deterministic mutations solely to exercise recovery. It is
not the authentic agent demonstration. It tests capture/restore boundaries,
interrupted Rust checkpoint and branch preparation, dirty/untracked preservation,
source corruption/link rejection, gate contention, a killed coordinator with a
live child, running-limit refusal, original/sibling isolation and cleanup.
Both `FORKLIFT_PAIR_TEST_FAILPOINT` (pair boundaries) and `FORKLIFT_FAILPOINT`
(database boundaries) require the opt-in `test-failpoints` build feature. Neither
variable injects failures in the normal release binary. These are process-exit tests, not a
host power-loss experiment. Gates are advisory and only exercised with controlled
synchronous commands.

Automatic retention, named deletion and object garbage collection remain unsupported.
Other planned, unimplemented work includes managed long-running application adapters, paired
export and broader app fixtures. The paired coordinator is now
part of the Rust CLI. Cloud hosting, automatic database merge and a general agent platform
are outside this milestone. Cache reuse still reads and verifies every byte;
source checkpoint capture interrupts its database. No exact APFS savings or
subsecond end-to-end guarantee follows from this experiment.

## Native CLI and compatibility

```sh
# Begin with an existing ready checkpoint named seed in .forklift.
# WORKSPACE must be a new directory outside SOURCE and STORE.
target/release/forklift pair workspace init --source fixtures/invoice-app --store .forklift --checkpoint seed
target/release/forklift pair workspace capture baseline
target/release/forklift pair workspace run -- python3 app.py
target/release/forklift pair workspace restore baseline
target/release/forklift pair workspace inspect
target/release/forklift pair workspace stop
```

`--pg-bin`, `--copy-mode full`, and `--no-cache` use the existing database
configuration/backend rules. After initialization the workspace's saved store
path selects its database store; the global `--home` option does not override it.
Without `--pg-bin`, the store's recorded PostgreSQL binaries are used. `run`
sets `DATABASE_URL`, the resolved `PG_BIN`, and `PYTHONDONTWRITEBYTECODE=1`.
It passes the child's input, output, error stream and exit status through; it
prints no JSON envelope even if the parent uses `--json`. Other pair commands
support the existing `--json` success/error envelope. Errors use exit status 1;
CLI usage errors use 2. Intentional test-build failpoints exit 99, including
underlying database failpoints now executing in the same process.

Workspace, current reference, saved-pair, and pending-journal formats stay at 1.
The Rust implementation accepts saved pairs and interrupted journals from the
retired Python implementation. The legacy integration test obtains that exact
implementation from Git commit `36cf2322b00c5d81d372888864e15b946e7e0fb7`, creates
owned workspaces in ignored test storage, then verifies native restore/recovery.
It needs that commit available locally. There is no active second Python
coordinator to maintain. Use the native commands for existing workspaces too.

File bytes and file permission bits are included in source inventory equality.
Directory permission bits are copied, but are not hashed in format 1. Metadata
is trusted local state, not an authentication boundary; additional path and
bounded-read checks do not turn this tool into a sandbox.
