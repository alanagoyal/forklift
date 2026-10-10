# Checkpoint costs: measurement and bounded SHA-256 optimization

Measured on 2026-10-10. The initial baseline identified real hashing costs, so the
implemented optimization enables the existing ARM SHA-256 backend in pinned
`sha2` 0.10.9. It retains every integrity read and check, and every durability
barrier. No lifecycle redesign or fingerprint-cache change is included.

At 128 MiB, repeated PostgreSQL capture fell **43%** (4.861 → 2.771 s) and
warm restore **16%** (3.565 → 2.997 s). Repeated native Supabase capture fell
**32%** (14.524 → 9.917 s) and restore **38%** (24.286 → 14.989 s). Each repeated
state has two samples; the tables retain first operations and observed ranges.

## Method and environment

The starting commit was `147bca693cfb8bb216715070b6770fb0507e0d4d`, verified as HEAD
and as an ancestor before edits. Pilot observations were recorded before selecting
the optimization; see [baseline observations](baseline-observations.md). Those
pilots include contention and are excluded from the comparison below.

The final serial matrix compares timing-only `111e096` against candidate
`b2fcd82`, release builds with `test-failpoints` enabled but no failpoint activated.
Each run's `environment.json` records executable SHA-256 identities. Both binaries
use the same timing scopes; the candidate additionally bounds diagnostic buffering.
The performance change is the aarch64-only `sha2/asm` feature (sha2-asm 0.6.4).
The upstream backend detects CPU support and retains a software fallback. Stored
hashes, artifact deduplication within a fingerprint scan, and storage formats are
unchanged.

Host: Apple M4, 16 GiB RAM, macOS 26.6.2, arm64, Python 3.14.6, Rust 1.95.0.
Plain PostgreSQL is Homebrew 17.11. Native Supabase uses CLI 2.120.0 and native
PostgreSQL 17.11 (clang 21.1.7, pinned artifact 17.11.0.004-r1). These are local
APFS measurements. **fsync, full_page_writes, synchronous_commit, and database page
checksums were verified on.** There is no fsync-off comparison.

Each size has a fresh private fixture, three captures and three restores. Source
has 64 deterministic files totaling 1 MiB. Native fixtures additionally select a
private explicit PG17 config and Booklist's unchanged checked-in migration (66
selected files / 1,107,506 bytes total). Booklist's original PG15 config is untouched.
Its unchanged checked-in dump script runs against two synthetic books and people;
the larger payload is isolated in another schema. Each requested MiB inserts 512
rows containing 2 KiB deterministic text plus row/index overhead. Native Storage
adds one distinct 1 MiB object per MiB. Database, source and Storage mutations are
checked after restore, and each size is also restored by the opposite binary
outside the timed samples.

Run order was PostgreSQL before, PostgreSQL after, native before, native after;
within each run, sizes were 1, 32, 128 MiB. All operations ran serially. The other
Forklift thread held heavy tests during this window; no compilation or other
benchmark ran concurrently. Desktop background activity remains uncontrolled and
load averages are included per sample. This is a small, ordered comparison, not a
randomized trial or a production latency guarantee.

Native artifact caches were cloned privately before setup; downloads are excluded.
No OS cache purge was performed. "First" refers to the first loaded capture or the
first restore of sample_0, **not cold disk cache**. PostgreSQL's first restore is a
materialization-cache miss; the next two are hits. Repeated captures reuse packs.
Native snapshots do not use packing/compression or the PostgreSQL materialization
cache. Fixture verification between samples also warms caches. Each cache-miss
restore has n=1 per size/build, so its variation is unknown. Repeated states have
n=2; ranges below expose that limited evidence. Setup is timed separately from
capture/restore and includes fresh store/stack preparation, but excludes subsequent
synthetic loading. APFS clones are measured; full-copy mode is supported by the
script but was not measured in this matrix.

## Results

Values are median seconds (minimum–maximum; sample count), except first
operations which have one observation.

### Plain PostgreSQL

| Payload MiB | Operation/state | Before | After |
| --- | --- | --- | --- |
| 1 | capture first | 2.101 (n=1) | 1.918 (n=1) |
| 1 | capture repeat | 2.106 (2.098–2.114; n=2) | 1.916 (1.892–1.940; n=2) |
| 1 | restore first | 7.567 (n=1) | 7.226 (n=1) |
| 1 | restore repeat | 2.882 (2.834–2.930; n=2) | 2.868 (2.858–2.878; n=2) |
| 32 | capture first | 2.950 (n=1) | 2.477 (n=1) |
| 32 | capture repeat | 2.704 (2.691–2.717; n=2) | 2.103 (2.096–2.109; n=2) |
| 32 | restore first | 7.948 (n=1) | 7.397 (n=1) |
| 32 | restore repeat | 3.000 (2.987–3.012; n=2) | 2.874 (2.843–2.905; n=2) |
| 128 | capture first | 5.869 (n=1) | 4.406 (n=1) |
| 128 | capture repeat | 4.861 (4.858–4.864; n=2) | 2.771 (2.766–2.777; n=2) |
| 128 | restore first | 9.550 (n=1) | 8.198 (n=1) |
| 128 | restore repeat | 3.565 (3.556–3.574; n=2) | 2.997 (2.993–3.002; n=2) |

### Native Supabase

| Payload MiB | Operation/state | Before | After |
| --- | --- | --- | --- |
| 1 | capture first | 12.746 (n=1) | 9.671 (n=1) |
| 1 | capture repeat | 12.816 (12.691–12.940; n=2) | 9.284 (9.068–9.499; n=2) |
| 1 | restore first | 19.944 (n=1) | 13.249 (n=1) |
| 1 | restore repeat | 19.750 (19.681–19.820; n=2) | 13.261 (13.212–13.311; n=2) |
| 32 | capture first | 13.922 (n=1) | 9.718 (n=1) |
| 32 | capture repeat | 13.307 (13.241–13.373; n=2) | 9.412 (9.294–9.530; n=2) |
| 32 | restore first | 21.645 (n=1) | 14.309 (n=1) |
| 32 | restore repeat | 21.061 (21.026–21.097; n=2) | 13.882 (13.868–13.895; n=2) |
| 128 | capture first | 15.177 (n=1) | 10.256 (n=1) |
| 128 | capture repeat | 14.524 (14.437–14.611; n=2) | 9.917 (9.879–9.955; n=2) |
| 128 | restore first | 24.737 (n=1) | 14.427 (n=1) |
| 128 | restore repeat | 24.286 (24.256–24.316; n=2) | 14.989 (14.975–15.004; n=2) |

### Phase evidence at 128 MiB

These are median **inclusive** milliseconds for the two repeated samples. Rows
can overlap and must not be summed. Raw spans and the derived summaries also
provide exclusive times. A dash means the phase is absent from those samples.
Native snapshot/replacement parents include hashing as well as cloning/fsync;
the APFS clone rows show that cloning itself did not account for their decrease.

**postgres**

| Phase | Capture before → after ms | Restore before → after ms |
| --- | --- | --- |
| Service shutdown command | 113.6 → 113.8 | 114.0 → 113.9 |
| Clean-shutdown control verification | 11.9 → 12.2 | 11.2 → 11.3 |
| Source copy including nested hashes/fsync | 896.9 → 895.5 | 1807.0 → 1787.3 |
| Source hashing | 55.1 → 36.1 | 158.5 → 89.6 |
| APFS data clone (all trees) | — | 95.3 → 93.9 |
| Data-tree fsync (all trees) | — | 30.8 → 30.6 |
| Object capture including nested work | 3149.9 → 1038.7 | — |
| Cache data hashing | — | 752.9 → 232.4 |
| Runtime fingerprint scans | 131.0 → 125.7 | 146.7 → 145.2 |
| Serial file fsync (all scopes) | 865.0 → 870.3 | 1633.1 → 1655.7 |
| Directory fsync (all scopes) | 93.8 → 94.1 | 128.3 → 121.0 |
| Metadata publication incl. fsync | 104.1 → 114.4 | 117.4 → 116.9 |
| Publication incl. nested work | 13.0 → 11.8 | — |
| Start/readiness incl. nested work | 194.6 → 200.2 | 197.3 → 196.2 |

**supabase**

| Phase | Capture before → after ms | Restore before → after ms |
| --- | --- | --- |
| Service shutdown command | 1536.4 → 1422.8 | 1540.6 → 1417.0 |
| Clean-shutdown control verification | 9.0 → 9.3 | 13.4 → 13.6 |
| Offline page checksum verification | 203.0 → 285.7 | 681.5 → 687.8 |
| Source copy including nested hashes/fsync | 302.0 → 313.9 | 1155.3 → 1184.4 |
| Source hashing | 25.2 → 13.8 | 90.5 → 56.2 |
| Database snapshot including clone/fsync | 1008.2 → 501.7 | 959.9 → 485.8 |
| Storage snapshot including clone/fsync | 333.7 → 183.9 | 329.0 → 189.5 |
| Database replacement including clone/fsync | — | 1060.3 → 564.5 |
| Storage replacement including clone/fsync | — | 343.5 → 116.3 |
| APFS data clone (all trees) | 249.4 → 243.6 | 485.8 → 485.0 |
| Data-tree fsync (all trees) | 141.0 → 133.1 | 233.4 → 222.2 |
| Data tree hashing | 2123.2 → 849.7 | 5318.0 → 2072.3 |
| Runtime fingerprint scans | 2209.7 → 989.3 | 6546.5 → 2934.2 |
| Runtime file SHA-256 (also CLI identity) | 4628.9 → 1217.7 | 8455.5 → 2334.0 |
| Serial file fsync (all scopes) | 273.9 → 290.5 | 1042.2 → 1096.4 |
| Directory fsync (all scopes) | 43.4 → 36.0 | 94.8 → 112.6 |
| Metadata publication incl. fsync | 9.3 → 10.4 | 26.7 → 28.9 |
| Publication incl. nested work | 4.3 → 4.4 | — |
| Start/readiness incl. nested work | 4131.3 → 3682.1 | 3205.9 → 3010.0 |

### Packing, compression and cache misses

PostgreSQL 128 MiB capture counters below are milliseconds. First capture has n=1;
repeat is the median of n=2. These existing counters overlap phase spans.

| Counter | First before → after | Repeat before → after |
| --- | --- | --- |
| Data hashing | 1820.7 → 403.5 | 1287.0 → 279.4 |
| Compression | 961.5 → 951.1 | 4.0 → 3.9 |
| Existing-object validation | 106.8 → 31.5 | 1582.6 → 568.5 |
| Object write/sync | 1099.9 → 1081.6 | 35.3 → 33.0 |

The first capture scanned 395,469,776 bytes and wrote 85 new packs (about 158.5 MB)
in both builds. Repeated captures wrote 3 then 1 new packs; most bytes were reused
and still validated. Compression and durable writing remain similar while hashing
and validation decrease, supporting the intended mechanism.

For the first 128 MiB restore, the outer object-materialization span was 6.790 →
5.362 seconds, including nested reconstruction, hashes and fsync. All serial file
fsyncs across that restore totaled 5.476 → 5.736 seconds; pack read/decompression/
verification totaled 1.077 → 0.624 seconds. These overlap the materialization span.
This leaves durable file writes as a substantial cache-miss cost. The first-restore
sample count is too small to establish a fsync performance change. Native
Supabase snapshots have no packed-object compression phase.

### Service availability bounds

Median seconds at 128 MiB for repeated operations (n=2). These are trace-derived
bounds, not a request-level availability measurement.

| Backend / operation | Before stopped lower / maintenance upper | After stopped lower / maintenance upper |
| --- | --- | --- |
| postgres capture | 3.178 / 3.495 | 1.069 / 1.400 |
| postgres restore | not applicable | not applicable |
| supabase capture | 7.832 / 12.587 | 4.241 / 8.656 |
| supabase restore | 15.095 / 19.842 | 8.395 / 12.822 |

### Dataset and setup receipts

Sizes below are apparent bytes, not unique physical allocation after APFS clones.
Counts are the baseline pre-measurement fixture inventories. The native stacks
include runtime database state in addition to the selected application database.

| Backend | Payload MiB / rows | Application DB bytes | Data files / bytes | Storage files / bytes | Setup before / after s |
| --- | --- | --- | --- | --- | --- |
| postgres | 1 / 512 | 9,377,459 | 977 / 42,093,429 | — | 7.81 / 7.30 |
| postgres | 32 / 16,384 | 54,621,875 | 979 / 120,892,281 | — | 7.38 / 7.25 |
| postgres | 128 / 65,536 | 194,737,843 | 987 / 395,225,981 | — | 7.45 / 7.22 |
| supabase | 1 / 512 | 18,781,875 | 1,939 / 84,990,973 | 1 / 1,048,576 | 33.93 / 24.78 |
| supabase | 32 / 16,384 | 64,067,251 | 1,941 / 147,082,282 | 32 / 33,554,432 | 25.30 / 34.46 |
| supabase | 128 / 65,536 | 204,273,331 | 1,946 / 371,265,836 | 128 / 134,217,728 | 34.40 / 23.94 |

Each native runtime artifact inventory has 7,296 files / 813,792,567 apparent
bytes. This inventory is not a claim that every file is read by each fingerprint;
the scanner retains its existing artifact selection and canonical-path deduplication.

## Interpretation and next measurements

Runtime fingerprinting was a measured cost, not an assumed one: native restore
performs several scans, each already deduplicating canonical artifact paths.
The change accelerates the same SHA-256 work rather than adding an invalidation
policy or skipping compatibility checks. Source copying and durable writes remain
visible costs after hashing improves. Plain PostgreSQL capture's clean-shutdown
control check and native capture's additional offline page-checksum verification
are separately instrumented; the backends do not perform identical checks.

The service windows above distinguish database availability from command latency.
The lower bound starts after the shutdown command returns and ends at entry to the
next start call. The upper bound starts on shutdown entry and ends after readiness.
Neither measures client requests. PostgreSQL restore starts the replacement before
parking the old database, so no corresponding global all-databases-down interval
exists; clients still must reconnect to the selected endpoint. Native restore does
compatibility and saved-state validation before shutdown, which explains some of
the difference between total latency and the stopped window.

These fixtures have no persistent application server. An application cooperating
with source/database capture must remain paused for the operation; total command
time is the maintenance-budget reference, not a measurement of application outage.
Application stop/restart/readiness is unmeasured here. The separately implemented
managed-app feature was integrated after this matrix, so these numbers describe
the explicitly identified benchmark commits, not managed application restart.

Prioritized follow-ups (planned, not implemented):

1. Measure larger source/file counts and PostgreSQL cache-miss restores, where
   per-file durable writes remain expensive. Evaluate bounded concurrency only
   with the same publication ordering, error handling, crash tests and fsync
   guarantees. Do not remove durability barriers to improve a benchmark.
2. Profile native launch/readiness and shutdown with a real managed app. Add
   client probes to measure actual request unavailability, rather than treating
   conservative service-window bounds as client-observed downtime.
3. Expand to full-copy mode, multi-GiB datasets, more samples and reversed build
   order. Explicitly cold OS-cache and uncached artifact setup are separate
   experiments; neither was established here.
4. Revisit repeated fingerprint scans only if remaining measured cost warrants
   a safe invalidation design. The current per-scan canonical-path memoization
   remains intact. No cross-operation cache is justified by these results alone.

## Validation and reproduction

The candidate passed the complete contributor suite, including corruption,
recovery, lifecycle, materialization cache, retention, legacy compatibility,
project onboarding and release failpoint rejection. Native integration checked
Auth, private Storage, Realtime, unchanged Booklist migration/dump, six recovery
boundaries and worktree isolation. Independent Python hashlib vectors at padding,
block and streaming-buffer boundaries passed with hardware and forced-software
SHA-256. The matrix also verifies cross-build restore in both directions at all
three sizes for both backends. All matrix fixture servers stopped successfully.

After merging the managed-app feature, runtime revision `30a8f0b` also passed
formatting, all-target unit tests, all-feature clippy, project integration, managed
PostgreSQL and native acceptance, the full native integrity suite, and the normal
release's managed failpoint rejection. Subsequent CI work changed only test
fixtures/drivers: full native acceptance with explicit synchronization barriers
(`2cf7e4b`) and focused native partial-start recovery with broken proxy settings
(`091bee7`) passed locally. The final focused native recovery regression also
passed with reverse DNS disabled and broken proxies (`31f3106`). These are
validation runs, not additional performance
samples. Their receipts identify runtime and test revisions separately.

Sanitized receipts are in [validation](validation/); the benchmark directories
contain `environment.json`, compressed raw `samples.jsonl.gz` and derived
`summary.json`. Timing records contain only fixed labels and numeric metrics,
never arguments, SQL, credentials or runtime command output. Environment receipts
contain local fixture paths and content hashes for reproducibility.

Use [the methodology and command reference](../../docs/benchmarks.md#checkpoint-costs).
Build baseline `111e096` and candidate `b2fcd82` separately with
`cargo build --locked --release --features test-failpoints`. Save both executables.
For each backend, run `scripts/benchmark_checkpoints.py` with
`--sizes-mib 1,32,128 --samples 3 --copy-mode apfs`, `--bin` selecting one executable,
`--verify-bin` selecting the other, and `--build-revision` naming the selected build.
Use a new output directory for each run. Native additionally requires the pinned
CLI, a reusable cache (which the script clones), and the unchanged Booklist path.
Run the four combinations in the documented order without concurrent heavy work.
Then pass each raw `.jsonl.gz` to `scripts/summarize_checkpoints.py`. Nested phase
spans overlap; the summarizer subtracts direct child time for exclusive totals.
Object counters overlap spans and are reported separately. Diagnostic output is
buffered until the operation finishes; subprocess elapsed time includes its final
serialization, while phase and service-window timings do not.
