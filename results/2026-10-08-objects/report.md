# Object checkpoint measurements — 2026-10-08

Recorded results from the named run, not a guarantee for other workloads or
current revisions. See [benchmark methodology](../../docs/benchmarks.md).

## Measured content reuse and costs

These are single correctness-run observations on one laptop, not percentile or
throughput benchmarks. Cache state was uncontrolled. The fixture's repeated text
and PostgreSQL's repeated/empty pages compress and deduplicate well; results must
not be generalized to arbitrary data. No comparison with Neon or DBLab was run.
An early debug run revealed repeated pack decoding; the final run used a bounded
four-pack cache and an optimized build.

| Capture | Scanned bytes | Bytes reused from previous objects | Newly written compressed packs | Capture time | Source interruption |
| --- | ---: | ---: | ---: | ---: | ---: |
| Initial checkpoint | 132,377,800 | 0 | 14,954,930 bytes / 26 packs | 1.176 s | 1.520 s |
| Same application data | 132,377,800 | 129,664,068 | 164,489 bytes / 1 pack | 0.994 s | 1.324 s |
| Added column and updated 1% of rows | 132,738,839 | 85,771,332 | 5,857,725 bytes / 12 packs | 1.167 s | 1.518 s |

The second capture reused **97.95% of scanned bytes** from previously saved
objects. It still needed new content because PostgreSQL's WAL and internal state
changed across restarts. This is content reuse, not an assertion of unique APFS
physical savings. Fixed 1 MiB boundaries also mean small row edits can change
much larger chunks. New-pack bytes exclude the new manifest and filesystem
metadata; complete repository costs are reported below.

For the initial capture, measured hashing took 0.649 s, compression 0.111 s and
object/manifest write-sync work 0.357 s. The second capture spent 0.450 s hashing,
0.002 s compressing, 0.023 s writing/syncing, and 0.467 s verifying reused data.
Reuse verification includes decompression and additional checksums. Timers do not
partition every instruction perfectly; total capture time is measured separately.

The first export took 0.618 s and occupied 15,315,282 apparent bytes, including
its manifest. Its first independent restore took 5.966 s **to a stopped branch**,
including materialization and PostgreSQL page checks; starting/querying it was
verified separately. A later valid export/restore after interruption tests took
0.425 s / 6.834 s. Startup is not hidden inside a claim of immediate queryability.
These operations still sync materialized PostgreSQL files individually.

## Complete retained storage accounting

`stats` counts the complete managed store, including failed/old generations and
orphan/temporary objects. The figures below are apparent regular-file bytes,
not unique physical APFS usage. File block counts are preserved in the raw data
with explicit `allocated_not_unique_bytes` labels. Directory/inode overhead and
peak usage during operations were not measured.

| Location / point in run | Materialized generations | Object repository | Total store, including catalog/other files |
| --- | ---: | ---: | ---: |
| Source, after first three checkpoints | 132,324,611 | 22,060,603 | 154,386,697 |
| Source, after all tests | 397,303,684 | 25,043,039 | 422,349,808 |
| Restore destination, after all tests | 396,817,017 | 0 | 396,820,249 |

The final object total includes retained work from interrupted captures; the
materialized totals include previous reset generations and failed restores.
Restore creates a materialized branch, not a second local object repository.
Outside these stores, the first valid export occupied 15,315,282 bytes and failed
export staging directories retained another 15,805,611 bytes. The test workspace
also retains a second valid export and deliberately damaged input copies; those
are test inputs outside the reported store totals. Nothing was garbage-collected
to improve the figures. Database files, packs and corrupt test inputs are ignored
locally; only small evidence records are committed.
