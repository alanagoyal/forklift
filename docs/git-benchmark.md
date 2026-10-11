# Local Git comparison

`scripts/benchmark_git.py` compares database checkpoints, with source in Git,
against a custom-format PostgreSQL dump in Git/Git LFS and a complete stopped
PostgreSQL data directory in Git/Git LFS. It creates every cluster and repository
under a **new** experiment directory. Never supply an existing database directory.
It uses only private Unix sockets, with TCP disabled, and configures no remotes.
The supported execution environment is macOS/APFS with PostgreSQL 17 and Python 3.

## Reproduce

Build before measuring:

```sh
$HOME/.cargo/bin/cargo build --locked --release --bin forklift
mkdir -p benchmark-data/tools
curl -fL https://github.com/git-lfs/git-lfs/releases/download/v3.7.1/git-lfs-darwin-arm64-v3.7.1.zip -o benchmark-data/tools/lfs.zip
# Expected SHA-256: 76260fb34f4ee622ff0a66b857e5954aa49c7e343a92e57a1ec4a760618c94b2
shasum -a 256 benchmark-data/tools/lfs.zip
unzip -q benchmark-data/tools/lfs.zip -d benchmark-data/tools
python3 scripts/benchmark_git.py \
  --root benchmark-data/git-comparison-final \
  --output results/2026-10-10-git-comparison \
  --sizes-mib 8,64 --samples 3
```

The private LFS binary's release checksum is published by
[Git LFS](https://github.com/git-lfs/git-lfs/releases/tag/v3.7.1).
The script does not install system/global hooks or change global Git configuration.
Use `--lfs-bin`, `--bin` and `--pg-bin` to override executable locations. A smoke run
uses new paths and `--sizes-mib 1 --samples 1`. Assertions must stay enabled.
PostgreSQL shared memory and local sockets require a suitable execution sandbox.

## Workload and contracts

The payload generator is reused from `benchmark_checkpoints.py`: 512 rows per
requested MiB, each with 2 KiB of deterministic MD5 hexadecimal text. This is
moderately compressible synthetic text, not random bytes or production data.
The invoice CLI application and seed are reused unchanged from `fixtures/invoice-app`.
Selected source adds the existing benchmark's 64 deterministic files (1 MiB).
All methods get identical logical contents, edits, and source bytes; physical
clusters have different identities, users and internal transaction histories.

Each method/size retains ten captures: initial, three unchanged, three small
updates (10 rows plus a source marker), and three cumulative schema edits (one
constant-default column and index plus a source marker each). All captures happen
before restores, so unchanged samples have no intervening user writes. Shutdown,
startup, PostgreSQL internal writes, and verification reads may still change
physical files. All history stays reachable; unchanged commits are allowed.

Then each historical state is restored after poisoning a source file and a row.
Baseline snapshot working files are removed and re-created from Git/LFS objects,
so restore measures actual object checkout, not reuse of a convenient snapshot copy.
The main runtime is stopped and removed before replacing it. Forklift uses `reset`
and `start`; its internal retention and materialization cache are left enabled.
Each restore is followed by a new detached Git worktree and independent database.
Forklift uses `branch --from` then `start`; baselines check out the commit and
materialize the dump or physical tree. This makes Forklift provisioning a warm
materialization-cache path; restore is the first materialization of that checkpoint.
No OS cache is purged. Neither path is described as constant-time or cold-disk.

All operations include correctness verification. The invoice CLI runs to completion
(no persistent HTTP server); payload rows are hashed in primary-key order, schema
is dumped without owners/privileges and with random restrict tokens normalized,
and every selected source file is SHA-256 checked. Expected fingerprints must
match across all five methods. Provisioning includes writes to each database and
the sibling source, checks isolation in both directions, and undoes the main probe.
Its verification is consequently more expensive than restore verification.
The extra worktree/database is stopped and removed outside operation timing;
storage is measured while it exists and again after cleanup.

This measures usable local development state for one database with no custom
roles, extensions, tablespaces, external WAL, replication slots, or other databases.
A `pg_dump` is not a whole-cluster backup. The logical baseline is sufficient for
this application, but is not equivalent to preserving every cluster-level property.
Forklift project capture/restore, managed apps and native Supabase are not measured.
Source is coordinated by this serial harness; no concurrent application writes
occur. It is not a generic atomic multi-writer source/database capture protocol.

## Lifecycle and storage choices

- Every PostgreSQL server has fsync, synchronous_commit, full_page_writes and page
  checksums enabled and verified after capture/restore/provision. Initdb uses its
  normal sync. No initdb `--no-sync`, unlogged tables or durability relaxation.
- Physical baselines use `pg_ctl -m fast -w stop`, verify `pg_controldata` reports
  shut down, and copy the **whole** cluster with `cp -cR` on APFS. They restart the
  source before committing the stable copy. No copying a running cluster, WAL
  exclusion or partial database-file backup, following the
  [PostgreSQL physical backup rules](https://www.postgresql.org/docs/17/backup-file.html). The fixture has no symlinks/external
  storage. A tracked directory manifest restores empty directories and their
  permission modes; root PGDATA is 0700. Before each restored physical cluster
  starts, `initdb --sync-only` persists the reconstructed files and directories.
  Git alone does not preserve the directory metadata.
- Logical baselines use `pg_dump -Fc -Z 6` and a fresh checksum-enabled `initdb`,
  start, then single-process `pg_restore --exit-on-error`. PostgreSQL provides the
  dump's consistent database snapshot while the source remains online, as described
  in the [pg_dump documentation](https://www.postgresql.org/docs/17/app-pgdump.html). There is
  no parallel restore tuning. Using the same compressed archive for Git and LFS
  is a simple default, not an optimal Git delta representation; uncompressed or
  normalized plain SQL could change both speed and history size.
- Git uses compression 6, `core.fsync=committed`, `core.fsyncMethod=fsync`, and
  `gc.auto=0`. LFS tracks individual physical files (`data/**`), not a tarball.
  New LFS objects and their parent directories are explicitly fsynced before
  capture returns; Git core.fsync does not cover that store. The separate
  `lfs_sync` stage is subtracted from `git_commit` to avoid double counting.
  Its object clean/smudge operations are local; no remote or network transfer.
  Git/LFS publication is not subjected to power-loss testing. PostgreSQL's
  durability settings do not establish identical crash guarantees for all snapshot
  stores. Forklift's additional integrity/fsync work remains in its timing.
- Forklift uses its implemented 1 MiB chunks, up-to-4 MiB packs and zstd level 3
  compression, plus verified materialization and APFS cloning. No implementation
  or runtime tuning is introduced by this benchmark.
- After all operations, ordinary `git gc --prune=now` is timed separately (no
  aggressive tuning). Forklift's supported cleanup and GC are separately timed
  in the same maintenance operation. No saved snapshots are deleted. Git fsck,
  LFS fsck, and stopped baseline page-checksum verification run outside timing.

`storage.jsonl.gz` records accumulated sizes after each capture, with a second
worktree/database, and before/after maintenance. Bytes are **apparent file lengths**,
not unique disk usage. Total includes local Git/LFS objects, snapshot working
files, source, live clusters, logs, and Forklift's object/cache/retained-generation
store. Git and LFS subtotals overlap: LFS objects are inside `.git`; do not add
all columns. After-maintenance totals have one runtime and one main worktree.
Deleted second branches may leave Forklift generations until its maintenance.
No st_blocks-based unique-space claims are made: APFS clones, compression,
sparse files and filesystem metadata make apparent bytes different from physical
space. Peak temporary space, remote storage and network costs are unmeasured.

`commands.jsonl.gz` contains exact argv, stage, exit status, stderr (including
Forklift spans), elapsed time and stdout digests. `samples.jsonl.gz` contains
end-to-end durations and non-overlapping harness stages. Command logging itself
is included in stages; Forklift trace emission/logging adds instrumentation cost.
`setup.jsonl.gz`, `states.jsonl.gz`, `environment.json` and `SUCCESS` document
fixture setup, correctness, machine/build identities and cleanup. Setup and
compilation are excluded from operation results. Results are serial, warm local
observations, with three samples per change class, not confidence intervals.
Method order reverses at the second size; background load is recorded, uncontrolled.

Normal failures attempt to stop only owned servers. After a killed process,
inspect the experiment's `postmaster.pid` files before removing its root. For
baseline clusters use `pg_ctl -D ABSOLUTE_OWNED_PGDATA -m fast -w stop`; for Forklift
use the recorded `--home .../store stop main` (and `stop second` if present).
Do not kill unidentified processes or use an existing PostgreSQL service.

Generate median/range and stage tables from the receipts:

```sh
python3 scripts/summarize_git_benchmark.py results/2026-10-10-git-comparison
```
