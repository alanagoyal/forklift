# Integrated ordinary Git backend comparison

Historical experiment: the Git backend is archived, not shipped. The current CLI
uses chunked storage only. See [reproduction instructions](../../docs/git-backend-experiment.md)
to apply the experimental implementation in a separate checkout.

**Recommendation: keep chunked storage as the default; offer ordinary Git as an experimental alternative.** Both integrated backends passed the same source/database history and isolation checks. In this implementation, chunked capture was faster at every tested size/change class. Git packing greatly reduced loose-object history, but packed history was smaller only in the smallest fixture. Restore differences were much smaller than capture differences, and some individual initial/warm results favored Git. This is evidence about these implementations and fixtures, not a general proof that Git cannot store databases efficiently.

## Implemented behavior

The new backend stores whole PostgreSQL files as ordinary Git blobs, paths as trees, and parentless checkpoint commits in a private bare repository under the Forklift store. Select future captures with `forklift --home /private/store config --storage-backend git`; choose a store outside the application repository. The CLI does not automatically relocate an arbitrary user-selected store. Benchmark stores are siblings of their fixture application repositories.

A shared Rust repository abstraction leaves lifecycle ownership, runtime fingerprints, complete clean shutdown, generation publication, verified caches, connection selection, pins and project/pair journals in place. Each checkpoint retains its backend when the setting changes. Existing catalogs default to chunked; Git selection advances to catalog format 6 so older binaries refuse it safely. There is no destructive migration, application commit, live database checkout, remote or upload. Git exports convert through verified files into the existing chunked portable bundle format.

Git runs with fixed local configuration and a cleared environment, with hooks, filters, replacement objects and network protocols disabled. External manifest bytes, committed tree mapping and streamed file SHA-256 are verified. Publication syncs objects before refs, then refs before the canonical manifest and shared catalog. Restore uses the same per-file and directory sync work as chunked restore. A dedicated flock remains held by Git descendants after Forklift dies, preventing an orphan GC from racing a new writer. These are deliberate durability barriers and tested process-interruption semantics, **not certification of identical power-loss behavior**.

See [storage design and operating instructions](../../docs/git-storage.md) for representation, recovery, security, rollback and unsupported operations. Native Supabase filesystem snapshots remain a separate path; Git storage of Supabase volumes, service images or Storage files is not implemented. Native Git bundle export, remote transport, online physical snapshots, tablespaces and cross-platform PostgreSQL restore are also outside this implementation.

## Measurement method and provenance

The final primary run used source revision `e3a6e3283968cf7e26dac76c1183bef2913e5c88`, with no implementation/harness diff, and normal release binary SHA-256 `d091418d57bfd264b3a26287ed814315d43617044ac107fe3ff014b45411698e`. It ran from 2026-10-11 14:56:24 to 15:13:43 UTC on Apple M4, 16 GiB RAM, macOS 26.6.2/APFS, PostgreSQL 17.11, Apple Git 2.54.0 and Python 3.14.6. [Environment](environment.json) records commands, paths, executable hashes and harness identities. Compilation, fixture setup and regression suites were outside operation timings. Runs were serial, without another agent compiling or benchmarking; ordinary host background activity was not eliminated. Load averages are recorded per sample. No OS cache purge or cold-machine claim is made.

Both methods call the actual Forklift checkpoint/reset/branch/start/connection/cleanup/gc lifecycle. The deterministic invoice fixture has 64 generated source files (~1 MiB) and a payload table of 512 rows per nominal MiB, each with 2 KiB of md5-derived text. Payload sizes are 8, 64 and 256 MiB; initial `pg_database_size` was 19,658,419, 101,373,619 and 381,638,323 bytes respectively. These are identical logical fixtures, not byte-identical independently initialized PostgreSQL clusters. The text is compressible; this is not a broad production-data distribution or a multi-GiB stress test.

Each backend/size retains ten historical snapshots: initial once, then three unchanged captures, three edits to ten rows plus source, and three cumulative schema/default/index changes plus source. Each history state is restored after deliberate source/row poisoning, restored again after re-poisoning, and provisioned into a second source worktree and database with two-way write-isolation checks. Application CLI output, source files, schema and row fingerprints must match. PostgreSQL `fsync`, `synchronous_commit`, `full_page_writes` and data checksums are asserted on throughout. Stopped main clusters pass `pg_checksums --check`; Git repositories pass `fsck`.

Initial observations are **n=1**; each change class is **n=3**, sequential states rather than independent randomized experiments. Backend order is chunked/Git at 8 and 256 MiB and reversed at 64 MiB. All captures precede all restores. The receipts establish a cache build for every primary first restore and a verified cache hit for warm restore and subsequent provision: 30 builds and 60 hits per backend. The timed operations include correctness verification and subprocess overhead, not only storage work. Source coordination uses harness-only commits externally; project/pair journal performance is **not measured**. Actual dirty/staged selected source, index/HEAD preservation and journal recovery are covered by the separate Git project regression.

## End-to-end results

Seconds, median of the three small row/source edits. [Complete tables](tables.md) give minimum/maximum ranges, all initial/unchanged/schema states and non-overlapping harness stages; [raw samples](samples.jsonl.gz) retain every observation.

| Payload MiB | Backend | Capture | First restore | Warm restore | Independent provision |
| ---: | --- | ---: | ---: | ---: | ---: |
| 8 | chunked | 1.028 | 5.473 | 1.008 | 1.322 |
| 8 | Git | 1.684 | 5.698 | 1.070 | 1.434 |
| 64 | chunked | 1.548 | 5.992 | 1.279 | 1.980 |
| 64 | Git | 3.840 | 6.079 | 1.345 | 2.200 |
| 256 | chunked | 3.360 | 7.892 | 2.161 | 4.158 |
| 256 | Git | 11.193 | 8.325 | 2.281 | 4.300 |

Initial capture at 256 MiB was 6.930 seconds chunked versus 20.222 seconds Git. Unchanged capture medians were 3.344 versus 5.084 seconds; schema-change capture was 3.614 versus 5.337 seconds. The small row edit is relatively costly for Git because changed database files are stored whole before later explicit delta packing. The implementation also reads source bytes for SHA-256 and Git hashing, re-verifies stored blobs, validates repository contents and syncs accumulated object history. Those conservative choices are implementation costs; this experiment does not isolate an intrinsic Git lower bound.

The result is not uniformly one-sided: initial first restore at 256 MiB was 8.170 seconds chunked versus 8.064 seconds Git, and initial warm restore was 2.654 versus 2.310 seconds (each n=1). Schema warm medians were 2.295 versus 2.267 seconds. Some ranges overlap and the 64 MiB Git unchanged samples vary substantially. These small differences do not establish a stable performance advantage.

At 256 MiB row edits, the timed checkpoint-command stage was 2.335 seconds chunked versus 10.106 seconds Git; source commit was ~0.07 seconds and verification ~1 second. First-reset stages were 6.782 versus 7.253 seconds, warm reset 1.101 versus 1.193 seconds. Independent branch creation was 0.642 versus 0.753 seconds, while isolation verification itself took ~3.2 seconds. Stage medians need not sum to total medians. [Internal spans](internal-spans-largest-rows.json) show ~4.21 seconds of file fsync in first reset for **both** backends. No Git-only removal of shared sync work was used. Internal phase durations are inclusive and overlap across names; the summarizer merges same-name nested intervals rather than adding them twice.

## Storage and separately timed maintenance

History includes database objects/manifests plus source Git objects. Total includes the fixture working tree, store, retained generations and caches. All ten checkpoints remain rooted after maintenance; this does not measure deleting most history. Explicit maintenance runs source Git GC, Forklift generation/cache cleanup, and backend GC/packing. Each maintenance observation is n=1.

| Payload MiB | Backend | History before → after MiB | Total before → after MiB | Allocated accounting after MiB | Maintenance seconds |
| ---: | --- | ---: | ---: | ---: | ---: |
| 8 | chunked | 17.38 → 17.37 | 2730.58 → 84.58 | 84.79 | 1.965 |
| 8 | Git | 37.70 → 14.83 | 2750.90 → 82.03 | 82.23 | 3.945 |
| 64 | chunked | 85.52 → 85.51 | 8623.15 → 295.23 | 311.51 | 2.044 |
| 64 | Git | 227.75 → 85.90 | 8765.38 → 295.61 | 324.03 | 7.716 |
| 256 | chunked | 312.69 → 312.68 | 29010.16 → 1015.62 | 1032.11 | 2.406 |
| 256 | Git | 856.94 → 329.47 | 29554.42 → 1032.48 | 1057.61 | 22.642 |

The large pre-cleanup totals count old generations and caches left by many resets and independent branches. They are not required live working-set sizes. At 256 MiB, Git backend GC took 20.433 seconds, versus 0.096 seconds for chunked GC; shared cleanup took about 2.1–2.2 seconds. Git's packed history was ~5.4% larger than chunked at this size, despite reducing its own loose history by ~62%. At 8 MiB, its packed history was ~14.6% smaller. This mixed result does not support replacing chunked storage on a general space-saving claim.

Apparent lengths and `st_blocks*512` are **accounting**, not unique physical APFS consumption. Cloned blocks may be shared, sparse-file allocation differs, and filesystem metadata is omitted. [Raw backend accounting](backend-storage.jsonl.gz) separates database/source history and total apparent/allocated values before and after maintenance; the complete tables also show the final second database present.

## Post-maintenance restore supplement

After the primary run, the same binary restored the three historical row-edit states at every size/backend from the existing packed histories. The supplement asserts a fresh cache build followed by a verified hit for each state; it preserves the primary timing/footprint receipts separately. All 36 restore observations, 411 commands, original fingerprints, durability assertions and final stopped-cluster checks passed. There were nine cache builds and nine hits per backend. [Supplement tables](../2026-10-11-packed-restore/tables.md), [validation](../2026-10-11-packed-restore/validation.json) and [environment/commands](../2026-10-11-packed-restore/environment.json) retain the evidence.

| Payload MiB | Chunked first / warm seconds | Git first / warm seconds |
| ---: | ---: | ---: |
| 8 | 5.226 / 0.966 | 5.352 / 1.053 |
| 64 | 5.721 / 1.216 | 5.940 / 1.287 |
| 256 | 7.858 / 2.145 | 7.946 / 2.233 |

These n=3 medians show no large packed-read penalty in this fixture. They do not isolate delta decoding from cache, host timing and repository-size changes: do not interpret the modest before/after improvement as a causal packing speedup. The warm path reads the verified materialization cache, not Git deltas. No captures after packing were measured.

## Validation, limits and next decision

[Primary validation](validation.json) records 246 timed observations (60 each capture, first restore, warm restore and provision, plus six maintenance), 4,318 commands with zero exit status, 1,482 untruncated traces, 426 durability assertions and 60 matching cross-backend state records. All owned primary servers stopped. [Regression receipts](../2026-10-11-git-backend-checks/README.md) cover 18 Rust tests, Clippy with warnings denied, ten existing real-PostgreSQL suites, Git corruption/publication/export/retention/GC cases and the Git selected-source project suite. The final Git/project reruns include the descendant-lock fix; the deterministic parent-death test also verifies no surviving test Git descendants. Native Supabase and historical binary builds were not rerun.

The earlier [external comparison](../2026-10-10-git-comparison/report.md) and rollback revision `af9b513657965a8a1ebf472b0dd89a3ac2b7f72d` remain intact. The [pre-lock full pilot](../2026-10-11-integrated-backends-pilot/EXCLUDED.md), smoke run and failed Git-path discovery attempt are retained separately and **not pooled** into final results. Process exits/corruption are tested, but host power loss is not. Export conversion works but its latency/temporary-space cost is not benchmarked. Long retention chains, many GiB of incompressible data, other filesystems and machines, and sustained write-heavy use remain unmeasured. Interrupted initial-repository staging and unknown Git index lock files are conservatively retained for inspection, as documented.

Keep chunked as the default and preserve the explicit Git option for experiments or workloads where ordinary Git tooling is valuable. Further optimization should target measured costs while retaining the same publication, ownership and integrity contract. Shared restore-sync improvements must be assessed for both backends. Do not migrate existing stores based on this small synthetic benchmark alone; switching capture selection is reversible and does not convert old snapshots.

## Reproduce

Use new, unused fixture/result paths; the scripts intentionally create and mutate disposable fixtures. Run the normal release build, primary workflow, then supplement serially. The actual commands used were:

```sh
cargo build --locked --release --bin forklift
python3 scripts/benchmark_backends.py --root benchmark-data/integrated-backends-final --output results/2026-10-11-integrated-backends --sizes-mib 8,64,256 --samples 3
python3 scripts/summarize_backends.py results/2026-10-11-integrated-backends
python3 scripts/benchmark_packed_restore.py --fixtures benchmark-data/integrated-backends-final --primary-results results/2026-10-11-integrated-backends --root benchmark-data/packed-restore-control --output results/2026-10-11-packed-restore
python3 scripts/summarize_packed_restore.py results/2026-10-11-packed-restore
```

The scripts accept explicit PostgreSQL and Forklift binary paths. This run uses local APFS clone mode; it is not a portable filesystem benchmark. Compressed JSONL command receipts include argument vectors, contexts, elapsed times, exit codes, stderr timing spans and stdout hashes; potentially bulky output is represented by its hash. Summary generation is outside timing. Supplemental scripts were added after the primary revision; their exact hash is recorded in the supplement environment, and the final result commit retains them.
