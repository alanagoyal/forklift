# native Rust paired rewind: validation evidence

Recorded on 2026-10-08 against the milestone-seven working changes on top of
`36cf2322b00c5d81d372888864e15b946e7e0fb7`. macOS ARM64/APFS, PostgreSQL 17.11,
Rust 1.95.0. The normal release binary was used for the authentic agent demo.
This is a three-invoice command-line fixture, not a browser demo or size-scaling
benchmark. The current fixture README's command wording was updated after the
run; the recorded inventories and agent source preserve the actual tested bytes.

## Actual agent experiment

[Prompt](real/prompt.txt), [command](real/agent-command.json),
[transcript](real/agent.jsonl), [exit status](real/agent-exit.txt),
[agent source](real/agent-source/app.py), [migration](real/agent-source/migration.sql),
and [code diff](real/code.diff).

One actual Codex invocation completed this run. Its file-change receipt records
changes to the application and creation of the migration. Its successful command
receipt records `BEGIN`, `ALTER TABLE`, `UPDATE 1`, and `COMMIT`, followed by the
modified app's JSON and durability settings. Independent harness assertions
verified the result rather than relying on the agent's final message.

| Observation | Before | After agent | After Rust rewind |
| --- | --- | --- | --- |
| Application version | 1 | 2 | 1 |
| Visible invoice IDs | 1, 2, 3 | 1, 2 | 1, 2, 3 |
| `archived` column | absent | boolean | absent |
| Invoice 3 | original | archived | original |
| Outstanding cents | 17,500 | 17,500 | 17,500 |
| Source inventory | baseline hashes/modes | app edit + migration | exact baseline |
| Application assertions | pass | pass | pass |

The complete [restored observation](real/restored.json) equals the
[baseline](real/baseline.json), including independently computed source inventory,
schema, all rows, application output and durability flags. The
[modified observation](real/modified.json) differs in code, schema and rows.
All three observations report `on|on|on|on` for fsync, full_page_writes,
synchronous_commit and data_checksums.

As in milestone six, [running old code alone](real/code-only.json) did not crash
this additive-schema app. It left the archived column and row change behind.
The [summary](real/summary.json) confirms unchanged source and sibling rows;
the harness also checked that neither acquired the archived column.

## Recovery and compatibility

- [Native paired suite](recovery/test-results.json): 44 recorded CLI calls,
  nine exits injected at pair/database boundaries. It also checks source
  corruption, invalid metadata/name paths, hidden/untracked/executable files,
  empty directories, retention of old generations, capacity refusal, lock
  contention and gate inheritance after the parent is killed. Additional direct
  checks cover child output/status and unsuccessful child launch. Deterministic
  mutations in this suite are not presented as agent output.
- [Lifecycle suite](lifecycle/test-results.json): 84 recorded calls and six
  injected exits, including database recovery after immediate PostgreSQL shutdown.
- [Historical format compatibility](legacy/compatibility.json): the original
  coordinator from Git revision `36cf232` created and captured a real workspace.
  Rust restored it and recovered three old journals: capture after database
  capture, restore before publication, and restore after publication.
- [Normal-release probe](release-failpoint-probe.json): capture succeeded despite
  both explicit failure-injection variables, which are compiled out. The
  [restored app JSON](release-app.json) was forwarded directly despite parent
  `--json`; [stop receipt](release-stop.json) confirms it was parked afterward.

The pair gate refuses app access while a pending journal exists. Before durable
pair publication recovery preserves the old selection; afterward it keeps the
new pair. This remains cooperative coordination, not a filesystem transaction
covering arbitrary code, writers, and external services. No host power-loss test
was performed.

[Paired cleanup](recovery/cleanup.json), [legacy cleanup](legacy/cleanup.json),
and [demo cleanup](real/cleanup.json) report zero active clusters. The harnesses
also asserted no PostgreSQL pidfiles in their owned store generations; lifecycle
cleanup passed. All abandoned generations remain retained in ignored test data.

## Observed times, not comparative claims

| Operation | Single observation |
| --- | ---: |
| Baseline capture, whole native CLI command | 0.851 s |
| Database interruption during capture | 0.671 s |
| Scanned database bytes | 40,839,672 |
| Baseline restore, whole native CLI command | 5.446 s |

[Capture receipt](real/capture.json) and [restore receipt](real/summary.json).
Filesystem cache state is uncontrolled. This fixture is tiny and there was no
controlled language-performance experiment. Do not compare these values with
milestone six as a Rust speedup: its restore ran alongside other tests. Cache hits
still verify complete materialized bytes; unique physical APFS savings are not
measured.

See [the native contract](../../docs/paired.md),
[milestone-seven reproduction](../../docs/history/milestone-seven.md), and
[validation receipt](validation.txt). Python remains only for demo/test
orchestration and the example app; the selected pair, gate, capture, restore and
recovery protocol are implemented by `src/paired.rs`.
