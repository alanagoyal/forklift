# Forklift versus local Git database snapshots

Measured on 2026-10-10 (America/Los_Angeles). The complete result tables are
[generated from raw samples](tables.md); [reproduction and lifecycle details](../../docs/git-benchmark.md)
include exact commands and storage scope. This is a local development workflow
comparison, not a backup product certification.

**Git baselines work, and Forklift is not universally faster.** At 64 MiB,
Forklift's small-update capture (1.579 s) is close to physical LFS (1.564 s),
faster than dump LFS (2.042 s), dump Git (2.472 s), and physical Git (3.294 s).
But Forklift's first restores lose to every baseline at both sizes. Its warm
second-database provisioning beats dumps at 64 MiB, but loses to both physical
Git workflows; at 8 MiB, all baselines provision faster. Packed physical Git
history is comparable to or smaller than Forklift's retained objects.

## Measured operation costs

Seconds, **median [minimum–maximum], n=3**, for the small-row-update states.
All timings include verification. [The full tables](tables.md) retain initial,
unchanged and schema-change results separately; no initial samples are pooled here.

| Payload MiB | Method | Capture | First restore | Second worktree/database |
| ---: | --- | ---: | ---: | ---: |
| 8 | forklift | 1.062 [1.048–1.082] | 5.619 [5.222–5.725] | 1.403 [1.367–1.408] |
| 8 | dump-git | 0.461 [0.460–0.466] | 0.874 [0.864–0.880] | 0.987 [0.982–0.999] |
| 8 | dump-lfs | 0.539 [0.517–0.541] | 1.043 [1.032–1.045] | 1.112 [1.071–1.118] |
| 8 | physical-git | 1.256 [1.235–1.651] | 0.816 [0.792–0.841] | 0.927 [0.925–0.931] |
| 8 | physical-lfs | 1.197 [1.182–1.371] | 0.943 [0.916–0.956] | 0.984 [0.961–1.007] |
| 64 | forklift | 1.579 [1.571–1.598] | 5.913 [5.865–5.951] | 2.012 [1.992–2.012] |
| 64 | dump-git | 2.472 [2.467–2.535] | 1.905 [1.817–1.929] | 2.222 [2.208–2.239] |
| 64 | dump-lfs | 2.042 [2.039–2.063] | 2.140 [2.121–2.145] | 2.474 [2.456–2.483] |
| 64 | physical-git | 3.294 [3.275–3.714] | 1.443 [1.419–1.484] | 1.834 [1.820–1.855] |
| 64 | physical-lfs | 1.564 [1.529–1.578] | 1.206 [1.205–1.213] | 1.617 [1.599–1.631] |

Unchanged captures also perform real work: at 64 MiB their medians are Forklift
1.561 s, physical LFS 1.501 s, physical Git 1.640 s, dump LFS 2.030 s and dump Git
2.524 s. Schema-change capture medians are 1.603, 1.580, 1.833, 2.046 and 2.532 s
respectively. At 8 MiB, dumps capture fastest (roughly 0.45–0.54 s versus Forklift's
1.06–1.16 s). These warm measurements do not establish a size-independent cost.

The stage receipts explain much of the difference. For 64 MiB row-update states,
Forklift reset takes 5.500 s median, including about 4.100 s summed file-fsync
spans; verification adds 0.348 s. Its subsequent cached branch creation takes
0.466 s, while provisioning's independence verification adds 1.224 s. Physical
Git capture spends 2.544 s in Git commit; physical LFS spends 0.810 s there plus
0.023 s explicitly syncing new LFS objects. Dump capture spends about 1.43 s
producing the archive; imports take about 0.75–0.80 s. The baseline physical
restore sync stage is included (about 0.028–0.029 s on this APFS run). These are
implemented-path costs, not proof that the paths have identical crash behavior.

## Retained history and working copies

**MiB of apparent file lengths, not unique physical space.** Each row retains all
ten snapshots. Object history is Forklift objects plus source Git objects, or
Git objects plus LFS objects. Total includes history, runtime, source, snapshot
working copy, caches/old generations, logs and metadata. Totals shown here have
one main worktree/database; [raw storage receipts](storage.jsonl.gz) and the
[full tables](tables.md) also show the second worktree/database while present.

| Payload MiB | Method | Object history after maintenance | Total before → after maintenance | Maintenance seconds |
| ---: | --- | ---: | ---: | ---: |
| 8 | forklift | 17.38 | 2069.47 → 84.59 | 1.642 |
| 8 | dump-git | 22.99 | 113.30 → 95.01 | 0.862 |
| 8 | dump-lfs | 41.28 | 113.31 → 113.30 | 0.094 |
| 8 | physical-git | 13.00 | 168.78 → 146.58 | 1.195 |
| 8 | physical-lfs | 237.13 | 370.75 → 370.71 | 0.103 |
| 64 | forklift | 85.52 | 6541.56 → 295.23 | 1.573 |
| 64 | dump-git | 183.40 | 613.83 → 430.60 | 8.667 |
| 64 | dump-lfs | 366.54 | 613.74 → 613.74 | 0.103 |
| 64 | physical-git | 84.06 | 643.82 → 502.64 | 5.241 |
| 64 | physical-lfs | 612.22 | 1030.86 → 1030.81 | 0.110 |

Ordinary Git's loose physical objects shrink from 225.24 to 84.06 MiB at 64 MiB
when packed; dump Git shrinks from 366.64 to 183.40 MiB. Packing costs 5.241 and
8.667 s respectively and is not hidden inside capture. Git GC does not compress
LFS objects. Forklift cleanup/GC removes old generations and materialization caches:
its large pre-maintenance footprint is real apparent retained data, not omitted
from the comparison.

Forklift's 295 MiB final total versus physical Git's 503 MiB does **not** establish
superior history compression: their retained objects are about 85.52 and 84.06 MiB.
The Git workflow keeps an additional full snapshot working copy (about 209 MiB).
A direct-in-place physical Git workflow could avoid that duplicate apparent tree.
APFS clones share blocks, so neither these lengths nor st_blocks can quantify
unique physical savings. Peak temporary space, filesystem metadata, remote copies
and transfer costs are outside the accounting.


## What was measured

Apple M4, 16 GiB RAM, macOS 26.6.2 arm64/APFS; PostgreSQL 17.11, Git 2.54.0,
Git LFS 3.7.1, Python 3.14.6. Normal release Forklift at `671f166f7c555efe0cf89e135570349c922049cd`;
[environment.json](environment.json) identifies executable and harness SHA-256.
Compilation took 12.15 s and was excluded, as were tool download and fixture setup.
The benchmark does not change Forklift implementation code.

Five workflows use identical deterministic payloads, invoice application, and source:
Forklift database checkpoints with source in Git; gzip-level-6 custom `pg_dump`
archives in ordinary Git or Git LFS; and whole cleanly stopped PostgreSQL data
directories in ordinary Git or Git LFS. Physical copies use APFS cloning throughout.
Git compression is level 6, automatic GC is disabled during operations, committed
objects use fsync, and new LFS objects/directories are explicitly synced. Restored
physical clusters run `initdb --sync-only` before starting. All servers verify
**fsync, synchronous_commit, full_page_writes and checksums on**.

Each 8/64 MiB fixture contains 4,096/32,768 payload rows (2 KiB hex text each),
plus three invoices and about 1 MiB of source. Ten captures per method/size:
initial; three consecutive unchanged captures; three updates to ten rows; three
cumulative column/index changes. Row/schema changes also change a source marker.
Captures complete before restores, so unchanged samples have no intervening user
writes. Internal database state and archive headers can still change.

Every historical state is restored after poisoning live data and source. Git/LFS
snapshot working files are removed and reconstructed from saved objects. Each
restore is followed by an independent detached Git worktree/database, verified by
writes in both directions. Timings include invoice CLI execution, full payload
row digest, schema digest, selected source hashes and durability checks. There
is no persistent application server; startup here means PostgreSQL readiness and
successful CLI application execution, not HTTP readiness. Provisioning's extra
independence checks make its verification cost greater than restore's.

Forklift restore first materializes each checkpoint; provisioning then reuses that
verified cache. Neither is a cold-OS-cache experiment. Timings retain the full
integrity, runtime-fingerprint and sync work of the implementation. Methods run
serially in opposite order at the two sizes. Background load is uncontrolled and
recorded per sample; three repetitions are observations, not confidence intervals.

## What these results can support

Git and Git LFS can represent usable, independent database development states when
paired with correct lifecycle scripts. This benchmark implements and verifies such
scripts. Their operational work includes shutdown consistency, empty-directory
restoration, permissions, data import, durable file reconstruction, connection
isolation and source coordination. Forklift provides database-specific lifecycle,
chunked storage and cache management rather than making Git fundamentally unable
to version database bytes. Its project/managed-app integrations are outside this
measurement and should not be presented as measured benefits here.

The comparison does not establish general superiority, constant-time cloning,
unique disk savings, remote performance, or performance on multi-GiB production
schemas. The logical dump preserves this application's state, not every cluster
property: no custom roles, tablespaces, replication slots, extensions or extra
databases are present. Physical clusters differ in identities/internal history;
application rows, schema and selected source match. Dump owners/ACLs are excluded
from the schema comparison; there are no application-specific grants in this fixture.

Compressed custom dumps are a sensible shared default, but are not an optimized
Git representation. Normalized plain SQL, directory-format dumps, parallel import,
reusing a prepared empty cluster, or a stopped runtime directly inside a Git
worktree may improve a baseline. This harness retains a separate snapshot working
copy and live cluster for physical Git; APFS clone sharing is not reflected in
apparent-byte totals. LFS stores changed whole files without cross-version delta
compression; fresh archive headers can defeat unchanged-archive object reuse.
No remote exists and no LFS upload/download is measured. No power-loss testing
establishes identical snapshot-publication guarantees across the implementations.

## Evidence and checks

- [samples.jsonl.gz](samples.jsonl.gz): all measured operations, stages and load.
- [commands.jsonl.gz](commands.jsonl.gz): exact argv, command duration/exit, stderr,
  Forklift timing spans, and stdout size/hash.
- [states.jsonl.gz](states.jsonl.gz): source/schema/rows/app fingerprints, matched
  across methods; [setup.jsonl.gz](setup.jsonl.gz): setup costs and database sizes.
- [storage.jsonl.gz](storage.jsonl.gz): every retained-history step, two-worktree
  footprints, and before/after maintenance; [summary.json](summary.json): medians/ranges.
- [build.json](build.json): build/tool identity and smoke-test notes.

Two 1 MiB smoke runs exercised all methods. A subsequent partial run was stopped
before the final explicit physical-restore/LFS sync barriers were added; its
[labeled receipts](../2026-10-10-git-comparison-pilot/EXCLUDED.md) are excluded.
The final run's status and owned-server cleanup are recorded in
[environment.json](environment.json) and [SUCCESS](SUCCESS).

The final run passed **100 captures, 100 historical restores, 100 independent
provisions and 10 maintenance operations**. All 5,482 logged commands exited zero;
10 Git fsck checks, four current-HEAD LFS fsck checks, and eight stopped baseline
page-checksum checks passed. Cross-method fingerprints matched, all 412 Forklift
traces were untruncated, and no experiment-owned postmaster.pid remained. The
[validation receipt](validation.json) also checks non-overlapping stage totals and
binary/harness hashes. Python compilation and `git diff --check` passed. No Rust
implementation was changed, so a fresh full Rust regression suite was not run.

For the implementation choice, the evidence supports keeping chunked checkpoints
as a useful lifecycle/storage option, particularly for incremental capture without
a separate Git packing step. It does **not** justify claiming that saving databases
in Git is impractical or generally slower. The measured next optimization target
is first materialization's per-file sync/publication cost, with durability and
integrity preserved; improving that is planned work, not part of this benchmark.
