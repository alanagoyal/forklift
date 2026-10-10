# decision 001: native local branches first

Status: accepted for milestone two, 2026-10-07. The lifecycle CLI is now
implemented; see [the lifecycle report](history/milestone-two.md). Object checkpoints
were implemented in [milestone three](history/milestone-three.md). This decision records
the original milestone-one rationale. [Milestone four](history/milestone-four.md) now
adds a verified materialized cache and APFS cloning for object-backed branches.
The historical measurements and future-tense statements below describe milestone one.

Use unmodified PostgreSQL 17 on macOS ARM64, APFS file clones for stopped-cluster
branches, and (in milestone three) immutable object checkpoints for transport.
Do not implement a new storage engine or an object-backed query path now.

The experiment establishes that this path can copy a whole cleanly stopped
cluster, start independent servers, preserve conflicting schema/data changes
across clean restarts, and leave its source unchanged. See the measured report
and raw records in `results/`. It does not establish crash safety for Forklift's
future catalog, unique physical storage savings, or an advantage over Neon/DBLab.

## Snapshot contract

A branch is a whole cluster. Only clusters created and owned by Forklift can be
stopped. The experiment creates a fresh, private run directory, refuses existing
output directories, disables TCP, and uses a private Unix socket directory.
PostgreSQL runs as the current OS user with local trust authentication inside
that private directory. It is intended for local experiments, not hostile users
sharing an OS account.

A physical branch requires a successful fast shutdown (clients are disconnected;
Postgres rolls back unfinished transactions), no remaining `postmaster.pid`, and
`pg_controldata` reporting `shut down`. Files, including WAL, are cloned/copied
only after those checks. Links and special files are rejected. Files and
directories are synced before starting the copy. The seed is restarted before
the branch, so total time includes returning the source to service. This
interruption is part of the product contract. A SQL CHECKPOINT by itself would
not make copying a running directory safe.

Keep fsync, full_page_writes, synchronous_commit and page checksums enabled.
The test asserts these on every start. PostgreSQL owns transaction durability;
file syncing in this harness is not a complete atomic checkpoint publication
protocol. Tests exercise clean shutdown/restart, not power loss or process-kill
recovery. No external tablespaces or third-party extensions are supported.

## Resource contract

A parked branch has zero servers; a running branch owns its PostgreSQL parent
and worker processes. Each test uses 32 MiB shared_buffers and max_connections=20,
with at most two clusters running simultaneously. Memory measurements sum RSS
for one parent and its direct children after verification; shared pages can be
counted more than once. These are neither unique memory usage nor steady-state
idle measurements. No 100-running-branch scalability claim is supported.

File lengths measure apparent bytes. `st_blocks * 512` is recorded with an
explicit **not unique** label. APFS shares extents, so neither it nor `du` can
prove total unique physical space across clones. Exact shared extent accounting
is unresolved. Volume free-space deltas on this active laptop would also include
unrelated writes. Do not claim a measured storage reduction from this experiment.
WAL sizes are separate observations but already included in cluster sizes.
Objects, temporary peaks, and export costs are unmeasured because that subsystem
does not exist yet. Failed runs retain their files for inspection.

## Alternatives and prior art

Neon already supports local branches and direct reads through its storage
services. Its documented local topology includes a storage broker, pageserver,
safekeeper and Postgres compute. The source README was inspected at revision
`fa504217c61bbcaf5c512d75830564541f917f8f` (README blob
`d2b9ff7e55ef09b1e21c811e714541322e235b25`). This machine has Rust and Docker
(OrbStack, ARM64, server 27.5.1), but lacks `protoc` and `pkg-config` on PATH.
The documented native build needs additional dependencies, recursive sources,
and patched Postgres. We did not install/build/run that stack in this milestone.
Its idle memory, actual process counts, startup and branch-to-first-query times
are **not measured**. This is an incomplete optional baseline, not evidence that
Neon cannot run here. Follow its pinned instructions to complete that comparison.

DBLab already offers thin clones and reset. Its documented manual installation
uses Docker plus a Linux storage setup with ZFS (or LVM in the architecture).
This Mac's native APFS directory does not provide that setup. Docker is
available, but provisioning a Linux storage pool would introduce a different
storage baseline; DBLab runtime measurements are deferred. Neither local
operation nor copy-on-write branching is a new invention by Forklift.

A custom object-backed filesystem/page service would need correctness work far
beyond the problem demonstrated here. It adds no proven user benefit yet. Reopen
this decision if a meaningful requirement needs live noninterrupting snapshots,
remote lazy reads, or storage capabilities APFS cannot provide.

## Differentiation hypothesis and next gate

The hypothesis is a short, offline Mac workflow for branch, experiment, reset,
and portable checkpoint export, with clear parked/running states and transparent
resource costs. It may simply be a useful wrapper around native facilities.
Validate it with milestone two's usable commands before adding custom storage.
Checkpoint portability is still planned and initially means compatible Postgres
major, architecture, extension set and platform, not arbitrary machines.

The original milestone-two gate was to add a durable catalog, serialized mutations, process identity
checks beyond PID, recovery reconciliation, atomic state publication and explicit
reset semantics. The feasibility harness is intentionally not that CLI. On a
normal error it attempts to stop only its recorded clusters; force-killing the
harness can leave its servers running. A SUCCESS record means the checks passed,
not a recoverable catalog entry.

Sources reviewed 2026-10-07:

- [PostgreSQL 17 file-system backups](https://www.postgresql.org/docs/17/backup-file.html)
- [Pinned Neon build and local branching instructions](https://github.com/neondatabase/neon/blob/fa504217c61bbcaf5c512d75830564541f917f8f/README.md)
- [DBLab manual installation](https://v2.postgres.ai/docs/dblab-howtos/administration/install-dle-manually)
- [DBLab storage architecture](https://v2.postgres.ai/docs/database-lab)
