# Measuring checkpoint costs

The checkpoint benchmark measures `project capture` and `project restore` separately
in disposable Git repositories and local database stores. It never imports hosted
data. Native Supabase uses an explicit private PostgreSQL 17 configuration and an
APFS clone of the supplied runtime cache. Booklist's original PG15 configuration,
checked-in migration, and data-loading script are unchanged.

Build a release binary and run one benchmark at a time:

```sh
cargo build --release --features test-failpoints
python3 scripts/benchmark_checkpoints.py --backend postgres \
  --sizes-mib 1,32,128 --samples 3 --output /tmp/checkpoint-postgres
python3 scripts/benchmark_checkpoints.py --backend supabase \
  --supabase-bin /path/to/supabase-2.120.0/supabase \
  --supabase-cache /path/to/native-probe/home/cache \
  --booklist /path/to/booklist \
  --sizes-mib 1,32,128 --samples 3 --output /tmp/checkpoint-supabase
python3 scripts/summarize_checkpoints.py /tmp/checkpoint-postgres/samples.jsonl.gz
```

Output directories must be new. Fixtures remain under ignored `benchmark-data/`
for inspection; the script stops every fixture server in `finally` and records
whether cleanup succeeded. Use `--copy-mode full` for explicit full copies.
Neither mode changes PostgreSQL durability settings. The benchmark requires fsync,
full_page_writes, synchronous_commit, and database page checksums to be on.

Each size starts with a new store/stack. The synthetic database contains 512 rows
per requested MiB, each with 2 KiB of deterministic MD5 text, plus row/index/storage
overhead. Native Storage has one distinct 1 MiB object per requested MiB. Source is
64 files totaling 1 MiB. Booklist adds its unchanged complete migration and two
synthetic books/people; its unchanged dump script runs before measurements and
after each restore. The larger synthetic payload is in a separate schema, so the
Booklist dump remains small. This is not a production-size Booklist workload.

`environment.json` records versions, the measured executable's SHA-256, setup/cache
policy, and cleanup. `samples.jsonl.gz` records dataset sizes/file counts, wall time,
and nested phase spans. Only fixed timing labels and numeric metrics are emitted;
command arguments, SQL, credentials, runtime status, and file paths are excluded
from the timing stream. Fixture location in environment metadata is not a secret.

## Interpreting the measurements

`FORKLIFT_TIMING=1` emits one JSON record on stderr after the command's work. Normal
stdout and command results are unchanged. Timing is disabled by default. Spans
use monotonic time and overlap when a phase calls another phase. The summarizer
subtracts direct children to report exclusive time; do not sum inclusive spans.
Parallel data-file fsync workers are covered by the parent `tree_fsync` wall time,
not summed worker durations. Source copy includes content copying, permission
updates and validation; its nested source hashes and fsyncs can be separated.
Object capture also supplies its existing hash/compression/reuse/write counters.
These counters overlap spans and must not be added to them.

Total time includes admission, compatibility checks, source work, durability,
publication, and service readiness. The reported service window is a conservative
interval from entering shutdown to completing subsequent startup/readiness. It is
not a sampled client-observed outage: shutdown checks begin while the old service
may still accept requests. A separate lower bound runs from shutdown command
completion to entry into the next start/readiness call, during which services
are known to remain stopped. Native restore validates its saved state before this
window. PostgreSQL restore starts a replacement before parking the old database,
so it has no equivalent all-databases-down interval; the old endpoint still becomes
obsolete and clients must reconnect through the selected project connection.

Application maintenance is different again. These fixtures have no persistent app
server. A cooperating application must stay paused for the entire operation,
including source replacement. Application restart time is **not measured**. The
managed-app implementation was integrated after these measurements; its lifecycle
tests are recorded separately from the benchmark comparison.

First restore of `sample_0` is a PostgreSQL materialization-cache miss; subsequent
restores of that same capture are hits. Capture 0 is first capture of the loaded
fixture, and later captures reuse existing object packs. Native captures are
filesystem snapshots, with no packed-object cache or compression. Runtime artifact
cache is populated before setup; first setup includes database creation, migrations
and enabling checksums. Download time is not measured. No OS page cache purge is
performed: neither "first" nor "cache miss" means cold disk cache. Repeated samples
are warm operational runs, and retained snapshots accumulate as in normal usage.

Plain PostgreSQL currently checks the clean-shutdown control state on capture;
it does not run a separate offline `pg_checksums --check` in that path. Native
Supabase does. Object restore validates chunk and file hashes. Timings describe
these implemented checks, not a claim that both backends run identical checks.

Results and bounded optimization decisions are recorded in
[the measurement report](../results/2026-10-10-checkpoints/report.md).

## Comparing builds

Save the baseline executable before rebuilding, or build the timing-only commit
`111e096` in a separate checkout. Pass `--bin` and `--build-revision` so the receipt
identifies the executable being measured, not just the current source checkout.
Use identical sizes, source counts, copy mode, cache policy and sample order for
both builds. Do not compile, run tests, or run another benchmark during sampling.
Record background contention and repeat affected runs instead of treating them as
an isolated speedup. Historical pilot results are labeled separately.

The measured candidate enables the pinned `sha2` crate's existing ARM backend.
Its [dispatch source](https://docs.rs/sha2/0.10.9/src/sha2/sha256.rs.html) selects
that backend on aarch64 only when the `asm` feature is enabled. The backend tests
CPU support and falls back to software when required. The algorithm, stored
SHA-256 values, artifact deduplication within a scan, and number of reads/checks
are unchanged. No durability call, shutdown check, or publication barrier is
removed. Independent digest vectors and the corruption/recovery suites check
compatibility; performance results belong in the report, not in this guarantee.
