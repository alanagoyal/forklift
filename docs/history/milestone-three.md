# milestone three: portable immutable checkpoints

> Historical document. Plans, commands, limitations and measurements describe
> the development stage recorded here. See the [history index](README.md) for
> context and links to current documentation.

Implemented and validated on 2026-10-08. Forklift now saves checkpoints as local,
compressed, content-addressed objects and exports self-contained directories.
A fresh store can restore a whole PostgreSQL cluster without the original working
cluster or object repository. Running PostgreSQL still reads ordinary files.

The [format specification](../checkpoint-format.md) describes compatibility, limits,
publication, failure behavior and storage accounting. The [walkthrough](../lifecycle.md)
shows the new `export`, `restore` and `stats` commands.

## What changed

New checkpoints use 1 MiB chunks, SHA-256 identities/checksums and Zstandard packs
containing up to 4 MiB of unique chunk data. Repeated content is reused before
new packs are written. Packs are durable before the immutable manifest is
published; the catalog marks a checkpoint ready only after that publication.

Export includes the manifest and its complete reachable pack set, builds in a
private sibling directory, then publishes without replacing an existing path.
Restore validates metadata before creating files, checks bounded pack decoding,
chunk/file hashes and PostgreSQL page checksums, then publishes a stopped branch.
It never overwrites an existing branch. Interrupted and rejected restores retain
failed generations for inspection and cannot be started.

Catalog version 1 upgrades atomically to version 2. Existing physical checkpoints
still support branch/reset; converting one to object storage requires creating a
branch from it and saving a new checkpoint. New object-backed branches/resets
materialize verified files. A native APFS cache for them is not implemented, so
no native-clone latency or shared live-file storage claim applies to this path.

## Validation

All final suites passed on macOS 26.6.2 ARM64/APFS with PostgreSQL 17.11 and Rust
1.95.0. PostgreSQL fsync, full_page_writes, synchronous_commit and page checksums
remained enabled. The recorded integration binary used the release profile with
opt-in interruption hooks; the ordinary release build also compiled successfully.

| Suite | CLI invocations | What it establishes |
| --- | ---: | --- |
| Object checkpoints | 98 | Content reuse, independent restore, integrity checks and interrupted publication |
| Lifecycle regression | 83 | Previous branching/reset/import/isolation and process-recovery behavior still works |
| Real legacy-store upgrade | 20 | A store created by milestone-two commit `f0e1042` upgrades and retains usable checkpoints |

The object suite used the deterministic 100,000-row fixture plus a second database.
Its 98 invocations comprise 64 successful commands, 25 expected rejections, and
nine abrupt test exits. It verified:

- An export restores while the **entire source store is renamed out of reach**.
  Data and schema digests match, and the second database's contents also survive.
- Branch/reset from the new object format preserve source/checkpoint isolation.
  A parked source returns to stopped after checkpointing or interrupted inspection.
- Corrupt manifests/packs/files, missing packs, oversized decoding declarations,
  overflowing offsets, traversal/absolute paths, case aliases, missing parents,
  symlinks, incompatible versions/builds/platforms and unsupported extension
  metadata are rejected. An existing branch/export is never replaced.
- Interruption halfway through an object write, after object publication, before
  manifest publication and after manifest publication never makes the incomplete
  checkpoint selectable. The source recovers its prior running state.
- Interrupted exports leave their final destinations absent. Interrupted restores
  during file writes or before catalog publication cannot be started. The original
  checkpoint still exports and restores successfully afterward.

Low-level Rust tests also check exact-repeat capture (zero new packs), independent
materialization, empty directories, bounded decompression and no-replacement
publication. Formatting, unit tests and strict Clippy checks passed. All clusters
in the final acceptance stores were stopped after testing.

The seed application checksum was
`100000:06f626459d036b980ef8ffbc27f4dc16`; its schema digest was
`844031264b087e57f99691a711865f12`. The second database returned `whole-cluster`.
The full checks and raw records are committed in
[results/2026-10-08-objects](../../results/2026-10-08-objects).

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

## Reproduce and limits

```sh
cargo test --locked
cargo clippy --locked --all-targets --all-features -- -D warnings
cargo build --locked --release --features test-failpoints
FORKLIFT_BIN=target/release/forklift python3 scripts/test_objects.py
FORKLIFT_BIN=target/release/forklift python3 scripts/test_lifecycle.py
cargo build --locked --release
```

The legacy suite takes an executable built from commit `f0e1042`:
`python3 scripts/test_legacy.py /path/to/milestone-two/forklift`.
All integration suites require PostgreSQL shared-memory permissions and create
fresh private stores under `benchmark-data/`. They stop their own servers and
retain evidence. Test hooks simulate CLI death at named boundaries, not actual
host power loss, disk loss, or every possible system-call interruption.

Portability is currently conservative: matching reported PostgreSQL version and
build configuration, matching platform/architecture, only built-in plpgsql, and
no external tablespaces. Cross-machine portability has not been measured. Export
checksums detect corruption, not an untrusted producer's intent. Automatic object
garbage collection, running-server limits, lazy object reads and native
materialization caching remain unimplemented. Milestone four is the running
limit and many-branch/resource benchmark work.
