# forklift project plan

> Historical document. Plans, commands, limitations and measurements describe
> the development stage recorded here. See the [history index](README.md) for
> context and links to current documentation.

## Purpose

Build a usable, impressive local Postgres experimentation tool. Optimize for a
small number of excellent capabilities, a real demo, and defensible measurements.
A company, managed cloud service, broad adoption, and an arbitrary 10x speedup are
not project goals.

The proposed experience: start from a populated database, fork isolated writable
branches, run migrations or agent experiments, reset or stop those branches, and
save checkpoints that can be restored in another compatible local installation.

A branch is a whole PostgreSQL cluster in v1, even if the demo uses one application
database. It has its own server process and connection endpoint while running.
We are not rewriting SQL, PostgreSQL, or its transaction engine.

## Working name and differentiation

**Forklift**: database forks and moving data. The name describes the core workflow
without promising cloud infrastructure. It remains a working name until any future
public release warrants a proper naming review.

Proposed distinctive combination:

- Local, offline operation on a developer's machine, starting with macOS ARM64.
- A short CLI workflow for branch -> experiment -> checkpoint/reset -> discard.
- Explicit parked versus running branches, with a configurable running limit.
- Portable, content-addressed checkpoints, with transparent physical storage costs.
- A repeatable demo of isolated experiments and recovery using real Postgres.

These are product hypotheses, not claims of invented techniques. Neon already
runs locally and branches databases; DBLab already offers thin clones and reset.
The first milestone must establish whether this combination is meaningfully
simpler or more useful on a laptop. If it is merely a wrapper around existing
capabilities, describe that honestly and refine the workflow before investing in
a custom storage engine.

## Language decision

Use **Rust** for the CLI, branch catalog, process management, storage adapters,
checkpoint format and instrumentation. It provides a deployable native program,
explicit memory control, and strong types for state transitions and storage
formats. Its complexity is justified if we proceed into storage work; it is not
a claim that orchestration alone needs Rust or will outperform other languages.

Keep **PostgreSQL**, written primarily in C, as the database. Start with unmodified
Postgres. Neon similarly combines Rust services with modified Postgres and C
extensions; we are not proposing to translate Postgres to Rust.

Use standard libraries/crates for CLI parsing, hashing, compression, serialization
and database connections. Pin a supported toolchain and dependencies after the
first environment check. Do not inherit a temporary toolchain path from greplcia.
A polished dashboard can come later; it is not required to validate storage.

## Architecture: establish the simplest useful local design

The initial implementation has four parts:

1. Rust CLI and a durable local catalog recording branches, checkpoints, ancestry,
   lifecycle state, format and Postgres versions, and owned data paths.
2. Unmodified local Postgres processes, one per running branch, using normal disk
   files and PostgreSQL's own write-ahead log (WAL: its crash-recovery change log).
3. A copy-on-write backend: share existing disk blocks until a branch modifies
   them. Investigate APFS cloning on the target Mac; Linux reflink support later.
   Capability detection is mandatory. A full-copy fallback must be clearly
   reported and never counted as shared-storage performance.
4. A local object repository containing immutable checkpoint chunks and manifests.
   Objects initially live in an ordinary directory on disk. No S3 server or
   network connection is required. Optional S3 transport can be added later.

The API boundaries should permit a different storage backend without changing
branch identities and commands. Do not build all possible backends in advance.

### A deliberate distinction about object storage

In this first design, active Postgres reads local files. Checkpoint objects provide
portable, deduplicated saved state. This is NOT yet Neon-style direct database
reads through an object-backed page service. Restoring onto a different disk may
materialize a complete cluster before it can run; measure that cost explicitly.

The first technical investigation compares this design with a local Neon setup.
If reading directly from shared object packs is essential to the interesting
result, record that decision before implementing a custom backend. Such a backend
would require either a carefully implemented filesystem/block layer or Postgres
storage integration; it is a separate substantial milestone, not a small option.
Avoid promising a new page service, a filesystem, and polished UX simultaneously.

### Snapshot correctness and lifecycle

V1 checkpoints and forks use a cleanly stopped source cluster. A normal checkpoint
SQL command alone does not make a live directory safe to copy. Stop Postgres,
verify shutdown, capture all required files, publish the destination, then restart
as requested. Report source interruption as part of branch creation time.

Only Forklift-owned clusters can be stopped or mutated. External databases enter via
an explicit logical import or a supported physical backup workflow. Never copy
arbitrary live PGDATA directories. Live noninterrupting branches are deferred.

Proposed states: creating, stopped, starting, running, stopping, failed, deleting.
Serialize branch mutation and maintain process identity beyond PID alone. On
restart, reconcile actual child processes and durable records rather than assuming
all recorded servers are alive. Unique ports/sockets and data directories prevent
cross-branch connections or writes. Default to local-only access.

A reset stops the target and atomically replaces its saved state with the selected
checkpoint before restarting. It discards later branch writes and must be explicit.
Deleting a branch never deletes its parent or a checkpoint still referenced by
another branch. Do not hard-link mutable PostgreSQL data files.

### Immutable checkpoint format

Versioned manifests map safe relative paths to file metadata and ordered content
chunks. Checksums detect corruption; hashes identify shared content. Pack small
chunks where measurements justify it. Keep original snapshots immutable.

Persist all object data before publishing a manifest. Use durable temporary files,
atomic replacement where supported, and appropriate file/directory synchronization.
The catalog must never advertise an incomplete checkpoint as ready. Validate paths,
lengths, checksums, and compatibility before restore; reject traversal and unsafe
links. Missing objects fail restore explicitly.

Record Postgres major version, architecture/platform constraints, required
extensions, and checkpoint format. “Portable” initially means another compatible
installation, not arbitrary Postgres versions or CPU architectures. Tablespaces
outside the managed cluster and custom extension installation are out of v1.

Keep previous valid checkpoints usable after interrupted export or restore.
Postpone automatic object garbage collection; include unused retained objects in
total storage figures. A later explicit cleanup command must trace references.

### Resource and durability contract

A parked branch has no running Postgres server; it is saved database state, not a
suspended process or preserved client session. Initially activation is explicit.
Automatic startup on connection requires a proxy and can be added after the core.
Running 100 branches still costs memory and CPU; a limit on concurrent servers is
part of the design. Never confuse 100 stored branches with 100 concurrent queries.

Target reliable recovery after process termination and host restart. Keep fsync,
WAL and normal Postgres durability enabled. A single local disk provides no
protection against losing that disk; replication and high availability are out of
scope. Distinguish PostgreSQL transaction durability from checkpoint publication.

## Proposed CLI experience

These are design sketches, not working commands:

```text
forklift init demo
forklift import demo --dump seed.dump
forklift checkpoint demo --name seed
forklift branch demo --from seed --name add-index
forklift start add-index
forklift connection add-index
forklift stop add-index
forklift reset add-index --to seed
forklift list
forklift stats
forklift export seed --output seed.forklift
forklift restore seed.forklift --name restored
```

Commands return useful human output and bounded JSON with consistent error codes.
The export format may be a directory or archive containing a complete reachable
object set; specify it before implementation. No hidden daemon is needed until
process ownership and startup-on-connect requirements justify one.

## Milestones and acceptance gates

### 1. Feasibility, comparison and contract

Status: completed on 2026-10-07 for the native PostgreSQL/APFS path. See
[the measured report](milestone-one.md) and
[architecture decision](../architecture-decision.md). Neon and DBLab runtime
baselines are deferred with setup gaps documented; no comparative claims are made.

- Confirm local Rust, Postgres, APFS/cloning and measurement capabilities.
- Pin one Postgres major version for the first implementation.
- Generate a deterministic SQL dataset and edit/migration workload.
- Measure ordinary copying, logical dump/restore and native copy-on-write cloning.
- Inspect/run local Neon where feasible; record installation friction, processes,
  idle memory and branch-to-first-query behavior. Review DBLab as prior art.
- Test stopped-cluster clones for isolation, restart and checksummed query results.
- Write an architecture decision: native clones plus object checkpoints versus a
  local Neon-based implementation versus custom direct object-backed storage.

Gate: one supported path works end to end with real Postgres; documented snapshot
semantics, resource accounting, and a specific differentiation hypothesis. Do not
write a new storage engine before this decision. Record unsupported baselines
and setup blockers instead of replacing them with estimates.

### 2. Usable local branch lifecycle

Status: implemented and validated on 2026-10-07. See the
[CLI walkthrough](../lifecycle.md) and [validation report](milestone-two.md).
Checkpoints are local physical copies in this milestone; object packs remain
milestone three. Reset/import use explicit `--yes`. Old generations and failed
entries are retained; deletion/garbage collection are not implemented.

- Implement init/import, checkpoint, branch, start, stop, connection, list and reset.
- Durable catalog, locking, process reconciliation, owned-path protections.
- Run two independent branches with conflicting schema and data edits.

Gate: branch A cannot change B or the seed; reset restores expected contents;
restart reconciles branches; failure cannot overwrite an existing branch; ordinary
Postgres clients connect successfully. Include source-stop and startup latency.

### 3. Portable immutable checkpoints

Status: implemented and validated on 2026-10-08. See the
[format/portability contract](../checkpoint-format.md) and
[validation report](milestone-three.md). Export is a self-contained directory.
New checkpoints use compressed object packs; milestone three reconstructed
verified files for each branch/reset. Milestone four adds a verified cache and
native cloning for these checkpoints.
Portability currently requires matching PostgreSQL version/build and platform.

- Implement versioned local object packs/manifests, integrity checks and export/restore.
- Reuse identical content across checkpoints; quantify hashing and packing overhead.
- Restore into a fresh owned directory without using the source working cluster.
- Inject interruption during object writes, manifest publication and restore.

Gate: restored query checksums and schema match the source checkpoint; incomplete
publication is never selectable; corrupt or missing data yields explicit errors;
storage measurements include both materialized clusters and retained objects.

### 4. Many branches on one laptop

Status: implemented and validated on 2026-10-08, including all 100 branch restart
checks and separately measured zero/one/three active clusters. Verified stopped
materialization cache, APFS clones, explicit full
copy/direct reconstruction options, configurable per-store running limit (default
three), and parked/running activity labels. See [the milestone-four report](milestone-four.md)
for completed validation and the measured scope. Automatic activation is deferred.

- Configurable cap on active Postgres processes and explicit parked state.
- Benchmark 1, 5, 20 and 100 stored branches with separately controlled active count.
- Measure activation, first query, resets, repeated experiments and divergence.
- Add connection-triggered activation only if it materially improves the demo.

Gate: memory tracks active work within measured limits; all branch data persists;
no hidden performance claims based on not actually starting databases.

### 5. Demo and honest report

Status: completed and validated on 2026-10-08 with 100,000-row and 1,000,000-row
demo runs. `scripts/demo.py` runs the complete 20-branch workflow and writes
actual query plans, operation timings, branch trees, process resources and storage
counters. `--inspect` queries current states on demand. See the
[reproduction guide](../demo.md) and [milestone-five report](milestone-five.md).
The separate 100-branch benchmark remains available with its milestone-four evidence.
Exact unique APFS sharing and competing-system runtime comparisons remain unmeasured.

- Reproducible setup, fixture generation, benchmark commands and raw data.
- A concise visual branch tree with live statuses and actual resource measurements,
  if the CLI and correctness work are already complete.
- Document supported OS/filesystem/Postgres version, limitations and recovery.

Gate: another run can reproduce the full demo; every metric comes from real
operations. Identify the practical distinction even if performance is similar to
native snapshots. Keep the repository private until a separate release decision.

## Ideal demo

1. Start with a substantial synthetic database, sized after checking laptop capacity.
2. Create 20 named experimental branches; show shared versus additional physical
   storage and the number of Postgres processes actually running.
3. Activate three branches. Add an index in one, change schema in another, and
   delete rows in the third. Query the seed to prove it is unchanged.
4. Compare a selected query's plans/results before and after the index change.
   Distinguish this SQL-level benefit from storage-engine performance.
5. Reset the destructive experiment and recover its original rows.
6. Save/export a checkpoint, stop processes, and restore into a fresh directory.
7. Show that expected data survives and display measured costs of each operation.
8. Run the scripted 100-stored-branch benchmark with a declared active limit.

Use a few real agents optionally to drive independent SQL experiments. The core
demo must run deterministically without model output or a cloud account.

## Benchmark method

Compare equivalent data, Postgres settings, durability, concurrency and freshness:

- Ordinary full stopped-cluster copies.
- Logical dump/restore as a familiar workflow baseline.
- Native copy-on-write snapshots/clones as the stronger storage baseline.
- Forklift with all catalog, checkpoint, startup and restore costs included.
- Local Neon/DBLab where runnable and comparable, without claiming a universal win.

Record machine, OS, filesystem, versions, dataset seed, table/index sizes, branch
count, active count, changed-data fraction, and source disruption. Separate:

- Branch metadata creation, disk cloning, Postgres startup, first successful query.
- Checkpoint scan/hash/compression/publication and restore/materialization.
- Actual allocated storage, apparent file sizes, shared blocks, WAL, object packs,
  retained checkpoints and temporary peak disk usage. Do not sum per-clone apparent
  sizes or ordinary du output and call it physical shared storage. Establish the
  platform's accounting method; report uncertainty when exact sharing is unavailable.
- Idle and active memory, CPU, p50/p95 latency across sufficient repetitions, query
  throughput, and reset/recovery time.
- Initial filesystem cache state versus repeated runs; do not call a new process
  a cold disk. Report any cache state that cannot be controlled.
- Read-heavy, small-write, larger-divergence and schema-change workloads.

Start with correctness, then a small performance fixture, then a larger database.
Publish slower cases and the cost of exporting portable checkpoints. No preset
speedup or space-saving ratio is required. A clear, useful workflow distinction
with credible measurements is a valid outcome.

## Explicit cuts

No distributed consensus, multi-region durability, managed cloud billing,
production tenancy, automatic merging of database branches, arbitrary timeline
rewind, unsaved session restoration, broad version/extension support, or Windows
support in v1. No automatic handling of external live databases. Live snapshots,
S3 sync, a proxy, a custom page service and a dashboard are separate extensions.

## First implementation task

Write the deterministic SQL fixture and a harness that creates, cleanly stops,
clones, restarts and verifies two real Postgres clusters on the target machine.
Measure native clone behavior and total branch-to-query time. This resolves the
main storage assumption before the CLI or object format becomes entrenched.

## Milestone 6 — paired application rewind

Implemented as the bounded [paired application coordinator](milestone-six.md)
over the existing Rust engine. One actual Codex experiment edits invoice application
code, applies a PostgreSQL migration and archives a row. Paired capture includes
all source files in the owned fixture; a cooperative command gate defines the
write boundary. Restore prepares new source and database generations and publishes
a pair reference only after both are ready, retaining the old work. Explicit
journal recovery chooses the old or new pair across interruptions.

Acceptance is recorded in [the milestone report](../../results/2026-10-08-paired/report.md):
baseline/modified/restored app observations, authentic agent receipts, original and
sibling isolation, real-PostgreSQL failure tests and stopped-server cleanup. The
Python prototype established the contract; milestone seven integrates it into the
Rust CLI. Persistent app adapters and paired export remain planned. External side effects are excluded. No cloud,
merge or agent-platform work is part of this milestone.


## Milestone 7 — native paired rewind

The paired coordinator now lives in `src/paired.rs` and is exposed as
`forklift pair WORKSPACE <command>`. It calls the existing lifecycle/storage
modules directly, keeps format-1 workspace compatibility, and owns source
capture, the inherited command gate, journal recovery, and pair publication.
Python remains for independent integration assertions and demo orchestration.

Acceptance: the original paired interruption scenarios pass through the Rust
CLI, actual historical Python workspaces can be restored/recovered, and a new
real-agent experiment verifies exact code/schema/data/application rewind. See
[the implementation and validation report](milestone-seven.md). No new
cloud, merge, long-running app adapter, or storage performance claims are added.


## Milestone 8 — reusable local-PostgreSQL project adapter

Implemented configuration-driven source ownership and argv tasks in the Rust
`forklift project` CLI. Per-worktree database branches share a machine-local seed
configuration. A durable journal extends paired publication to in-place source
restoration while preserving the Git worktree path, index and excluded files.
Tracked BB hook templates bind/park worktrees without assuming BB environment IDs.

Acceptance: two real Git project layouts using the same adapter; schema/row/source
rewind, worktree isolation, retained pre-rewind source, interruption recovery,
corruption refusal, gate contention and Git metadata preservation. See
[the milestone report](milestone-eight.md). Compatibility is explicitly limited
to projects using the native local PostgreSQL/socket setup. Automatic BB-agent
pausing, service supervision, nested exclusions and broader driver support remain
future work.


## Milestone 9 — real parallel agent experiments

Two authentic concurrent Codex sessions implemented different invoice-archiving
designs in separate Git worktrees and database branches. Both changed application
code, schema and rows, and verified their apps. After saving both experiments,
rewinding one restored its exact baseline while the sibling and original seed
stayed unchanged. Git metadata and unselected files were preserved, and all owned
servers were stopped. No production engine changes were needed.

The [milestone guide](milestone-nine.md) and [evidence report](../../results/2026-10-09-parallel/report.md)
record provider overlap, authentic receipts, observations and the reproducible
harness. The tracked BB hooks were exercised in Git-created worktrees; BB-native
provisioning, active-agent pausing, scale and performance remain unclaimed.
