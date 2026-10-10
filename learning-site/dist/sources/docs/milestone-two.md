# milestone two: usable local branch lifecycle

Implemented and validated on 2026-10-07. Forklift now has a usable Rust CLI for
creating and importing clusters, saving local checkpoints, branching, starting,
stopping, obtaining connection strings, listing state and resetting branches.
See [the walkthrough](lifecycle.md) for commands you can run.

A branch is an independent PostgreSQL 17 cluster. Checkpoints still consist of
ordinary local files copied from a cleanly stopped cluster, using APFS cloning
by default or explicitly requested full copying. **Object storage is not part of
this milestone.** Immutable object packs, portable exports and content integrity
validation are the next milestone.

## Implemented safety and recovery

The versioned catalog is published through file sync, atomic rename and directory
sync. Commands hold an exclusive store lock. Each generation has an ownership
marker; identifiers cannot escape the store. Existing names cannot be overwritten.
Startup and copying reject symlinks, hard links and special files. TCP is disabled;
private Unix socket directories separate running clusters. Normal clients connect
using the printed libpq connection string.

Process identity checks use more than a PID: OS start information, PostgreSQL
start time, executable, data directory and socket must agree. New server starts
force and assert fsync, full_page_writes, synchronous_commit and data_checksums.
The catalog records transient operations before doing the corresponding work.
Subsequent commands reconcile recorded state with actual processes.

Reset prepares a new generation before stopping the selected one. An atomic
catalog update selects the replacement. Old generations remain intact. Import
uses the same publication boundary and restores a trusted archive into a fresh
cluster inside one transaction. Both commands require `--yes`, and both preserve
the target's running/stopped state. Checkpoint temporarily stops a running source
and resumes it, including after interrupted operations on the next CLI command.

## Validation on real PostgreSQL

The final acceptance run used macOS 26.6.2, ARM64, APFS, PostgreSQL 17.11 and Rust
1.95.0. It ran the deterministic 1,000-row fixture through **83 CLI invocations**:
59 successful commands, 18 expected errors, and six deliberate abrupt exits.
All assertions passed. The test creates a store whose path includes spaces and
uses fresh CLI processes throughout, so the workflow depends on saved state
rather than in-memory state left by an earlier command.

Validated behaviors:

- Two simultaneous branches accept incompatible column types and different data
  edits; neither changes the seed or the other branch.
- Stop/start preserves data and schema. Reset restores the selected checkpoint,
  keeps the other branch unchanged, and preserves running or stopped status.
- Logical import matches the source fixture. A valid archive with corrupted row
  data fails during restore and leaves the original selected generation intact.
- Existing names, invalid names, wrong ancestry, unsafe links, duplicate generation
  identities, mismatched process identity, missing destructive-action flags and
  competing store locks are rejected. A pre-existing unowned directory is untouched.
- Interrupted init is marked failed and cannot be started. Interrupted start is
  reconciled to the actual server. Interrupted checkpoint is never selectable,
  and its source resumes. Interrupted reset before publication preserves the old
  contents; after publication it selects and resumes the replacement. Interrupted
  import before publication keeps the original branch.
- An external clean stop is reflected by list. PostgreSQL recovers committed data
  after `pg_ctl -m immediate` followed by start, using normal WAL recovery.

The six interruption points are opt-in test hooks that exit without cleanup.
They model the CLI dying at particular durable-state boundaries. They are not
host-power-loss tests or exhaustive interruption of every filesystem operation.
There are no active failure hooks in the ordinary release build.

The final suite also passed formatting, Rust unit tests, strict Clippy checks and
the normal release build. All database servers in the acceptance store were
stopped at completion. Raw results are in
[results/2026-10-07-lifecycle](../results/2026-10-07-lifecycle/); local run paths in
the JSONL records are normalized to `<run>`. Database files and logs stay under
ignored `benchmark-data/`.

## Observed operation costs

These are individual observations from the correctness run, not a repeated
performance benchmark or a speedup claim. Cache state and other laptop activity
were uncontrolled; the debug build had interruption-test support enabled.

| Operation | Total CLI time | Selected stages |
| --- | ---: | --- |
| Checkpoint a running source | 4.369 s | 3.949 s clone/sync; 4.307 s source interruption |
| Create a stopped APFS branch | 4.135 s | 4.060 s clone/sync |
| Create a stopped full-copy branch | 4.539 s | 4.443 s copy/sync |
| Reset a running branch | 4.650 s | 4.155 s preparation; 0.354 s stop-to-ready interval |
| Import into a stopped branch | 0.822 s | 0.011 s restore; includes initializing/starting/stopping the replacement |

Checkpoint's server-start stage was 0.126 s, followed by 0.024 s of identity and
durability verification. This “first_verified_query” event is an administrative
query, not an application checksum query or query-throughput measurement. A newly
created branch is stopped, so its creation time does not include starting it.
Total CLI timing includes lock/catalog/reconciliation work and timing overhead.
Reset keeps the old target available during replacement preparation. The selected
stop-to-ready interval includes the catalog switch and restart verification.

## Remaining limits and next milestone

Checkpoint and branch creation still require a cleanly stopped physical source.
Recovery runs on the next command, not automatically at boot. No live snapshots,
external tablespaces, extension installation, arbitrary PostgreSQL versions,
TCP authentication configuration, or automatic binary-upgrade workflow is offered.
Unresponsive or ambiguously identified servers fail closed and may need manual
inspection. Trusted local users can still edit their own files and run arbitrary
superuser SQL; this is not tenant isolation.

Old and failed generations are retained, including the previous data after reset
or import. Delete/garbage collection and failed-name recovery are not implemented.
There is no automatic running-server cap yet. Exact APFS shared-space accounting
is still unavailable; retained files must not be omitted from future storage-cost
claims. No new memory, throughput or many-branch scalability results are claimed.

Milestone three replaces local-only saved copies with a versioned immutable
checkpoint object format, export/restore, checksums and interruption/corruption
handling. Running PostgreSQL will still read ordinary files in that design.
