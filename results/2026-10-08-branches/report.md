# Stored branch measurements — 2026-10-08

Recorded results from the named run, not a guarantee for other workloads or
current revisions. See [benchmark methodology](../../docs/benchmarks.md).

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
[`results/2026-10-08-branches`](.).
