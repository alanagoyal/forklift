# milestone four: many branches on one laptop

> Historical document. Plans, commands, limitations and measurements describe
> the development stage recorded here. See the [history index](README.md) for
> context and links to current documentation.

Forklift now reconstructs an object checkpoint once into a verified, stopped cache,
then uses APFS file clones to create independent writable branches. Parked branches
have no running PostgreSQL server. A per-store limit defaults to three running
clusters and also counts temporary import servers.

The cache is an optimization of the existing design: PostgreSQL uses ordinary
local files, while portable saved checkpoints remain compressed immutable objects.
There is no new database storage engine, cloud service, background daemon or
connection proxy. See the [cache contract](../checkpoint-format.md) and
[command walkthrough](../lifecycle.md) for recovery and operational details.

## Measured run

Measurements are from the release binary on this Mac, using PostgreSQL 17.11,
100,000 deterministic rows, checksums, fsync, full_page_writes and synchronous_commit
enabled, 32 MiB shared_buffers per server, private Unix sockets, and no TCP listener.
The OS filesystem cache was uncontrolled; these are repeated local operations,
not cold-disk measurements. The fixture is small and is not a production workload.

The benchmark creates 1, 5, 20 and 100 experimental branches, plus one parked source.
It samples zero, one and three active clusters separately (three only when enough
branches exist), then writes and restarts every experiment branch. Distinct markers
and the original account-data digest are checked after each restart. Further
experiments change schema, update 1% and 10% of rows, reset all three, and repeat
five edit/reset cycles. The source is checked unchanged at the end.

The run completed on 2026-10-08 on an Apple M4 with 16 GiB RAM, macOS 26.6.2
and APFS. The application database occupied 41.2 MB; the stopped cluster cache
occupied 107.7 MB apparent, and the object repository 13.4 MB apparent.

| Operation | Samples | Median | p95 | Range |
| --- | ---: | ---: | ---: | ---: |
| Create a parked branch from a warm cache | 99 | 0.389 s | 0.446 s | 0.365–0.590 s |
| Start a parked branch (CLI completes) | 216 | 0.205 s | 0.217 s | 0.180–0.251 s |
| First application query via a new psql process | 216 | 0.013 s | 0.015 s | 0.009–0.027 s |
| Reset a running branch, including restart | 8 | 0.818 s | — | 0.809–0.879 s |

The first application query counts accounts; startup already performs internal
identity and durability queries. Start through connection lookup and that first
application query took 0.250 s median and
0.266 s p95 across 216 activations. Quantiles use nearest rank.
Warm branch stage medians were 0.203 s for full cache verification and
0.124 s for clone/sync plus destination checks; end-to-end timings include
catalog, process inspection and CLI overhead as well.

The first cache build plus a parked reset took **4.758 s** (one sample).
Direct reconstruction took **4.915 s** for the first branch and **5.182–5.192 s**
for two parked resets. Three subsequent cached APFS resets took **0.403–0.736 s**;
three cached explicit-full-copy resets took **0.405–0.449 s**. These small baseline
samples have fixed ordering, not randomized trials. They show the benefit of
avoiding repeated reconstruction and sequential per-file materialization syncs;
they do **not** show APFS outperforming full copying on this fixture. There is no
measured unique-disk-space saving claim. Larger fixtures remain future work.

Brief process-memory samples after a 0.5-second idle settling period:

| Stored experiments | Idle RSS, 0 active (MiB) | Idle RSS, 1 active (MiB) | Idle RSS, 3 active (MiB) | Read-workload RSS, 3 active (MiB) |
| --- | ---: | ---: | ---: | ---: |
| 1 (+1 source) | 0 | 34.3 | — | — |
| 5 (+1 source) | 0 | 33.2 | 101.9 | 186.7 |
| 20 (+1 source) | 0 | 33.2 | 99.5 | 180.8 |
| 100 (+1 source) | 0 | 33.1 | 99.7 | 179.1 |

One idle cluster had six processes, three had eighteen; during the read workload
these became seven and twenty-one. Three short `ps` samples were averaged for
each idle cell. Read-workload cells are single samples during two-second pgbench
runs with one client per cluster. This supports the limited observation that
PostgreSQL process memory followed the active count rather than the parked count.
Parked files may still occupy the OS filesystem cache, which this accounting does
not measure. No connection sessions are preserved while parked.

All **100 unique markers and original account digests passed after restart**.
Schema/1%/10% divergence, three resets, five additional edit/reset cycles, and the
unchanged source also passed. Attempts to activate a fourth cluster failed at the
configured limit. All servers were stopped at completion.

The store reported 11.971 GB apparent at 100 branches, rising to 12.825 GB after
persistence/divergence/resets. Totals include baseline copies and all retained
previous generations, not just the 101 selected branches. Final `st_blocks`
accounting was 12.844 GB, explicitly **not unique APFS physical storage**. The
cache remained 107.7 MB and objects 13.4 MB. Raw JSON records include each stage,
resource samples and storage counters under
[`results/2026-10-08-branches`](../../results/2026-10-08-branches).


## Correctness and recovery

The real-PostgreSQL acceptance suite covers:

- First cache construction, verified reuse, explicit full-copy and direct-object paths.
- Sibling/source isolation and cache immutability across edits and running resets.
- Same-length cache corruption and unexpected files: quarantine and reconstruct.
- Unsafe links, wrong ownership and a cache pidfile: refuse without copying or signaling.
- Abrupt exits before cache publication, after publication, and after branch copying.
- Default/configured limits, refusal to lower below the active count, and invalid limits.
- Admission and interrupted recovery for imports at the limit, plus parked checkpoint refusal.
- Deferred restart recovery, explicit cancellation by stop, and catalog migration.
- Valid cache reuse with a missing object pack, while export/direct reconstruction fail.

The earlier lifecycle, object corruption/publication, portable restore and real
milestone-two migration suites were rerun. Unit tests, formatting and Clippy
also passed: five unit tests and 270 acceptance/regression CLI invocations,
including 19 injected exits. These checks exercise process interruption and PostgreSQL WAL
recovery; no host power-loss experiment was performed.

File copying now overlaps up to eight independent file-sync calls. All files must
finish syncing before directories and catalog publication. Reconciliation writes
the catalog when state changes, avoiding one redundant catalog rewrite per parked
branch per command. Neither change disables PostgreSQL durability.

## Reproduce

```sh
export PATH="$HOME/.cargo/bin:$PATH"
cargo test --locked --all-features
cargo clippy --locked --all-targets --all-features -- -D warnings
cargo build --locked --release --features test-failpoints
FORKLIFT_BIN=target/release/forklift python3 scripts/test_cache.py
FORKLIFT_BIN=target/release/forklift python3 scripts/test_lifecycle.py
FORKLIFT_BIN=target/release/forklift python3 scripts/test_objects.py
cargo build --locked --release
FORKLIFT_BIN=target/release/forklift python3 scripts/benchmark_branches.py
```

The legacy test additionally takes a milestone-two executable as its first
argument. All scripts create private stores under ignored `benchmark-data/`,
attempt to stop their own servers on failure, and retain data and JSON evidence.
Allow PostgreSQL shared memory and process inspection when using a sandbox.
Do not run other benchmarks simultaneously. `FORKLIFT_ROWS` changes the fixture
size for subsequent benchmark runs; it was 100000 in this report.

## Limits and next milestone

Only three simultaneous servers were benchmarked. One hundred stored branches
is not a claim about one hundred simultaneous databases or queries. Idle and
read-workload samples sum parent/worker RSS, which can count shared pages multiple
times; they are not unique memory usage or total system memory. `ps` CPU samples
are instantaneous observations, not a controlled CPU-cost benchmark. The brief
pgbench runs are workload context, not a throughput comparison against another
system.

Apparent file bytes and allocated block counters include the cache, retained
previous generations, WAL, objects and interrupted work. They do not measure
unique APFS physical bytes, exact sharing, or temporary peak disk usage. There is
no deletion/garbage collection yet. A cache hit still hashes every file and is
therefore proportional to checkpoint size. Cached branches do not scrub object
packs; portable export/restore independently verifies them.

The next milestone is a concise, reproducible demo and honest report of the
complete branch/experiment/reset/export workflow. Automatic activation, live
snapshots, S3 transport, a custom page service, and runtime Neon/DBLab comparisons
remain separate work. No comparative advantage over those products is claimed.
