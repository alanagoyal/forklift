# milestone six: rewind an agent's code and database together

Implemented: a bounded Python application-workspace coordinator over the existing
Rust storage/lifecycle engine, an invoice fixture, a real coding-agent experiment,
and real-PostgreSQL interruption tests. This is an exploration inspired by
Supabase's database-per-worktree feature: **rewind an agent's code and database
together**. Inspection explains what changed; locality and Rust supply mechanisms.
It is not a competitive benchmark or a general agent platform.

The [recorded experiment](../results/2026-10-08-paired/report.md) shows source,
schema, data, application output, authentic agent transcripts, timings and limits.
The storage format and Rust CLI remain unchanged. The new user entry point is
[`scripts/paired.py`](../scripts/paired.py), not a new Rust subcommand.

## Implemented contract

A workspace is newly created and owns its source directories. Initialization
copies an explicitly selected small source tree and forks an existing database
checkpoint into a private branch. It never runs Git reset/clean on the user's
checkout. All files in that source directory are captured, including dirty,
untracked and ignored files; there is no implicit Git exclusion policy. Do not
put secrets or dependencies there unless you intend to retain them in snapshots.
Git metadata, symlinks, hardlinked files and special files are rejected. Limits
are 10,000 entries and 16 MiB per file. Empty directories and file permission bits
are preserved; ownership, timestamps, ACLs and extended attributes are not part
of the restore contract. The repository's unrelated `learning-site/` is excluded
by selecting only `fixtures/invoice-app/` as the initial source.

Every controlled application/agent invocation uses `paired.py WORKSPACE run --
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
limit, restore fails closed and recovery keeps the old pair. It does not silently
raise the limit or stop unrelated branches. Repeated restores retain old source,
branches, checkpoints and caches. There is no garbage collection.

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
python3 scripts/paired.py benchmark-data/my-pair/workspace inspect
python3 scripts/paired.py benchmark-data/my-pair/workspace run -- python3 app.py
python3 scripts/paired.py benchmark-data/my-pair/workspace stop
```

After an interruption, first run `paired.py WORKSPACE recover`. If an experiment
fails before `finish`, use that recovery command and `paired.py WORKSPACE stop` to
park the selected app. For the dedicated demo store, inspect `forklift --home
benchmark-data/my-pair/store list` and stop any other running owned branches before
removing files. A failed initializer can leave an owned partial store; inspect it
with the Rust CLI rather than retrying into existing output directories.

## Validation and planned work

```sh
cargo fmt --check
cargo test --locked
cargo clippy --locked --all-targets --all-features -- -D warnings
cargo build --locked --release --features test-failpoints
FORKLIFT_BIN=target/release/forklift python3 scripts/test_paired.py
FORKLIFT_BIN=target/release/forklift python3 scripts/test_lifecycle.py
cargo build --locked --release
```

The paired suite uses deterministic mutations solely to exercise recovery. It is
not the authentic agent demonstration. It tests capture/restore boundaries,
interrupted Rust checkpoint and branch preparation, dirty/untracked preservation,
source corruption/link rejection, gate contention, a killed coordinator with a
live child, running-limit refusal, original/sibling isolation and cleanup.
Coordinator injection is explicit via `FORKLIFT_PAIR_TEST_FAILPOINT`; Rust injection
still requires the `test-failpoints` feature. These are process-exit tests, not a
host power-loss experiment. Gates are advisory and only exercised with controlled
synchronous commands.

Planned, not implemented: packaging the coordinator into the Rust CLI, managed
long-running application adapters, paired export, safe deletion and broader app
fixtures. Cloud hosting, automatic database merge and a general agent platform
are outside this milestone. Cache reuse still reads and verifies every byte;
source checkpoint capture interrupts its database. No exact APFS savings or
subsecond end-to-end guarantee follows from this experiment.
