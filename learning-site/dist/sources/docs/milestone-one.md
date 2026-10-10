# milestone one: feasibility results

Completed 2026-10-07 for the native Mac path. The correctness gate passed with
real PostgreSQL; native cloning is feasible. **No substantial performance win is
established.** Logical dump/restore was fastest on this small fixture. The chosen
next step is a usable lifecycle CLI, not a new storage engine.

## What was run

Mac16,10, ARM64, 10 logical CPUs, 16 GiB RAM, macOS 26.6.2, internal APFS SSD with
FileVault. Around 119 GiB of container free space was observed before testing.
Rust 1.95.0 is pinned; the dependency-free Rust harness used PostgreSQL 17.11
(Homebrew). PostgreSQL major 17 is the implementation compatibility target.
The initial shell lacked Rust on PATH and native Postgres; the existing rustup
installation was used and PostgreSQL 17 was installed. No database service was
enabled. The sandbox initially blocked initdb's shared-memory allocation; the
successful experiments ran with the required OS access.

The smoke run used 1,000 rows and one repetition of each method. The recorded
run used 100,000 deterministic rows, five repetitions per method, and 15 successful
trials in total. The fixture table plus indexes occupied 33,390,592 bytes; the
database occupied 41,219,763 bytes. The stopped source cluster's apparent size
was 107,575,131 bytes, including WAL and system databases. The payload repeats a
hash eight times, so it is compressible; this matters for the compressed logical
dump baseline. Do not extrapolate to large or incompressible databases.

All three paths use the same fixture and durability settings. Each physical path
stops the seed, copies/clones all files, syncs them, restarts the seed, starts the
branch, and verifies application data and schema. Logical dump/restore leaves the
seed running, dumps only the fixture database, initializes and starts a fresh
cluster, restores it, and makes the same verification queries. Cluster-level
roles/settings are not portable through that logical baseline.

## Measured timings

| Method | Trials | Median branch → verified query | Min–max |
| --- | ---: | ---: | ---: |
| full-copy | 5 | 5.135 s | 4.916–5.396 s |
| apfs-clone | 5 | 4.905 s | 4.646–4.998 s |
| dump-restore | 5 | 1.070 s | 1.056–1.076 s |

Median source interruption was 4.814 s for full copy and 4.585 s for APFS clone.
Logical dump/restore did not stop the source. Median copy-and-sync time was
4.532 s for full copy and 4.299 s for clone. Logical dump, initdb and restore
medians were 0.212 s, 0.355 s and 0.176 s, respectively; individual medians need
not sum to the total median.

This harness issues an explicit sync for each copied/cloned file and directory.
The combined copy-and-sync stage dominates physical paths. Sync-only time was
not isolated, so its causal contribution is a hypothesis for later profiling.
These are measurements of this conservative harness, not optimized APFS snapshot
limits. `std::fs::copy` was deliberately avoided because it can use APFS cloning;
the full-copy baseline uses explicit reads/writes. Clone failure is an error,
never a silently substituted copy.

The overall timer includes source interruption and restart, destination startup,
data/schema verification, and intervening metric-write overhead. It excludes
initial fixture construction, later edit/restart checks, process sampling, and
creation of the sibling used for isolation testing. Startup includes a durability
settings query; “first verified query” comprises both data and schema queries,
not a bare SELECT 1. Each process snapshot follows those queries. There is no
hidden claim that source preparation or validation is free.

The cache was uncontrolled and likely warm. Methods ran in fixed full-copy,
clone, dump order on an active laptop. Five samples give a feasibility range;
they do not support p95, production throughput, cold-cache or statistical
superiority claims. A randomized larger-data benchmark belongs in later work.

## Correctness and resources

Every trial verified the baseline, altered one branch's schema and 1% of its
rows, and applied a conflicting column type plus 10% deletion to a sibling.
Both edited branches survived clean restarts with matching data and schema
checksums; the seed remained unchanged. The seed, edited destinations and
siblings passed PostgreSQL page-checksum verification. The fixture uses MD5
as a reproducibility checksum, not an adversarial integrity format.

The recorded seed digest is `100000:06f626459d036b980ef8ffbc27f4dc16`.
All branch-A data digests agree at
`100000:78c48842f8967e6e11438f4c08be94bd`; all branch-B digests agree at
`90000:88a89aa31683a78db896f697bf42a621`. Schema digests are included in the raw
checksums file. Tests compare initial branch digests with the seed and verify
edited digests across restart; the committed records allow comparison across
methods/runs. These tests do not exercise interrupted publication or host crash.

Every sampled branch had six PostgreSQL processes (parent plus five children).
Median summed post-query RSS was 34,336 KiB (copy), 34,352 KiB (clone), and
34,752 KiB (logical). Shared mappings may be counted repeatedly; this is not
unique RAM or steady-state idle memory. At most two clusters ran together; all
were stopped at completion. Edited cluster WAL apparent size was 50,331,648
bytes for each method, already included in total apparent cluster size.

Raw `st_blocks` values are recorded but **unique physical storage and shared
extent counts are unavailable**. Do not sum them to claim space savings. No
checkpoint objects exist yet, and no peak disk, steady-state CPU, query throughput,
or large-branch-count measurements were taken.

Neon's documented native build was inspected at a pinned revision. Missing build
tools and patched-Postgres setup are recorded in the architecture decision;
Neon runtime numbers are unavailable. DBLab's Docker/Linux ZFS/LVM setup was
reviewed but not provisioned. These are deferred optional baselines, not wins
against either product. The decision and differentiation hypothesis are in
[architecture-decision.md](architecture-decision.md).

## Evidence and validation

Raw evidence: [results/2026-10-07-100k](../results/2026-10-07-100k/).
It includes environment, all trial stages, process snapshots, checksum values
and the SUCCESS marker. Process snapshot trailing whitespace was normalized. Database files, dumps and verbose server logs are retained
locally under ignored `benchmark-data/`, not uploaded to GitHub.

```sh
python3 scripts/summarize.py results/2026-10-07-100k
cargo fmt --check
cargo test --locked
cargo clippy --locked --all-targets -- -D warnings
```

The filesystem test checks full-copy write isolation, refusal to overwrite an
existing destination, and rejection of symbolic links. Real database runs supply
the integration checks. Existing run-directory refusal was also verified against
the completed run, preserving its results. The next milestone still needs the
user-facing commands, durable catalog and robust process reconciliation described
in the project plan.
