# Initial baseline observations

These pilot measurements motivated the candidate; they are **not** the final
controlled comparison. Both pilots used release builds with the original
checkpoint algorithms plus opt-in timing. The starting commit was
`147bca693cfb8bb216715070b6770fb0507e0d4d`, verified as HEAD before edits.

PostgreSQL used three captures and three restores at each synthetic size. Each
source tree contained 64 files / 1 MiB. The first restore populated the materialized
cache; the next two reused it. Values below are elapsed seconds.

| Payload MiB | Capture median (range, n=3) | First restore (n=1) | Warm restore range (n=2) |
| --- | --- | --- | --- |
| 1 | 2.23 (2.06–2.40) | 7.66 | 2.84–3.15 |
| 32 | 2.63 (2.60–2.99) | 7.66 | 2.89–3.08 |
| 128 | 4.91 (4.70–5.90) | 9.97 | 3.63–4.55 |

Source copying accounted for roughly 0.75–0.91 seconds per capture and 1.5–1.9
seconds per restore, excluding its nested hash/directory-sync spans. The pilot did
not yet split source file fsyncs. Database reconstruction on cache misses accounted
for another 4.6–5.5 seconds excluding nested pack reads. This motivated adding
explicit file-fsync spans before choosing a change.

Native Supabase's first 1 MiB capture took 12.79 seconds, including a 10.83-second
conservative shutdown-to-readiness window. Its runtime fingerprint took 2.41
seconds, data-tree hashes 0.50 seconds, and source copy 0.30 seconds including
0.27 seconds of file fsync. The first restore took 19.55 seconds: three complete
runtime fingerprint scans accounted for 6.35 seconds inclusive; all runtime-file
hash calls (including repeated CLI identity hashing) totaled 8.20 seconds. Those
two numbers overlap and must not be added. The fingerprint already deduplicates
canonical artifact reads within each scan; that implementation was not removed.

Later native samples overlapped the managed-pause thread's native tests. Capture
ranged 12.79–16.67 seconds; restore ranged 19.55–29.64 seconds. The pilot was
interrupted through its cleanup handler, which confirmed all its servers stopped.
The PostgreSQL pilot also ran on a desktop with background activity, including
macOS media analysis. These observations identify costs, not reliable speedup
percentages. Final before/after sampling must run after heavy tests finish.

The selected candidate is enabling the existing aarch64 backend of pinned
`sha2` 0.10.9. Its default ARM path is software unless `asm` is enabled. This
changes how SHA-256 is computed without reducing the number of bytes read,
checks performed, or durable writes. Independent boundary vectors are tested on
both hardware and forced-software paths. Source/object fsync batching remains a
possible follow-up, not part of this candidate.

Raw pilot spans and environment/cleanup receipts are in `postgres-pilot/` and
`native-pilot/`. Their historical `revision` field identifies the checkout before
uncommitted timing edits, not a clean executable build; executable hashes are
retained. The final matrix instead records explicit clean build revisions. The PostgreSQL pilot additionally issued an explicit CHECKPOINT
during fixture loading. Final runs omit that privileged fixture statement on
both backends and rely on ordinary commits plus Forklift's clean shutdown.
