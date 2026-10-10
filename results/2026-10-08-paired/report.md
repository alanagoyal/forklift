# paired rewind: recorded application experiment

Measured on 2026-10-08, macOS 26.6.2 ARM64/APFS, PostgreSQL 17.11,
Rust 1.95.0 and Codex CLI 0.161.0. [Environment receipt](environment.txt).
The repository base was `33c207f832bb49d0dd3685c9390925a0e65a30a8`; the
Python coordinator, fixture and tests were uncommitted changes on that base.
This report records that prototype; use the native CLI for current workflows.

**An actual coding agent edited the invoice app, added a PostgreSQL column and
updated a row. Paired rewind restored the original code, schema, data and passing
application output.** This is a command-line application, not a browser demo.
No HTTP server was started.

| Observation | Baseline | After agent | After paired rewind |
| --- | --- | --- | --- |
| Application version | 1 | 2 | 1 |
| Visible invoice IDs | 1, 2, 3 | 1, 2 | 1, 2, 3 |
| Outstanding amount (cents) | 17,500 | 17,500 | 17,500 |
| `archived` schema column | absent | boolean, not null, default false | absent |
| Paid invoice 3 | original row | `archived = true` | original row |
| Application assertions | pass | pass | pass |
| Source inventory | baseline hashes/modes | app edited, migration added | exact baseline hashes/modes |

The entire recorded source inventory, column/type listing, rows and application
JSON after rewind equal the baseline. All three stages report
`fsync|full_page_writes|synchronous_commit|data_checksums = on|on|on|on`.
Source and sibling branches retain their original rows and have no `archived`
column. The full three-row dataset is intentionally small: this demonstrates
coherence, not database size scaling or production application coverage.

## Authentic agent evidence

The initial [prompt](real/prompt.txt), [command](real/agent-command.json), and
[transcript](real/agent.jsonl) record a real provider invocation. Its file-change
receipt identifies edits to `app.py` and creation of `migration.sql`. That attempt
reported a socket permission failure; its zero exit code was **not** treated as
successful acceptance. Its transcript contains that failure report but does not
expose a command receipt for the denied attempts.

A second bounded invocation used the [retry prompt](real/retry-prompt.txt) and
[retry command](real/retry-command.json), enabling socket networking in the agent's
workspace sandbox. The [retry transcript](real/agent-retry.jsonl) records schema
inspection, actual execution of `psql -f migration.sql` with
`BEGIN / ALTER TABLE / UPDATE 1 / COMMIT`, and successful `python3 app.py` output.
Thus two provider invocations completed one experiment; it was not a scripted
mutation presented as an agent run. No specific provider model is claimed by this
report because the JSONL receipt does not identify it.

The independently copied [agent source](real/agent-source/app.py),
[migration](real/agent-source/migration.sql), and [code diff](real/code.diff)
make the changes reviewable. The finish harness checks actual database and app
behavior independently of the agent's final message. The reusable runner now
requires both agent file-change and successful migration execution receipts before
accepting a real run; these recorded transcripts pass those checks.

Running the original application source alone against the modified database still
shows three invoices, but leaves invoice 3 archived and the added column intact.
The [code-only observation](real/code-only.json) demonstrates that distinction.
This particular additive migration does **not** make the old app crash. We do not
claim an observed crash; the demonstrated problem is that code-only undo leaves
database changes behind. Paired rewind restores the complete recorded baseline.

## Measured operation observations

| Operation | Single observed time |
| --- | ---: |
| Baseline paired capture, whole coordinator command | 0.809 s |
| Database source interruption during that capture | 0.640 s |
| Database object scan during capture | 40,839,672 bytes |
| Paired restore, whole coordinator command | 10.597 s |

The [capture receipt](real/capture.json) contains Rust stage timings, object metrics
and the pair's source hashes. The [summary](real/summary.json) records the restore
wall time and app/isolation observations. Restore includes preparing source,
creating a database branch/cache as needed, starting/verifying it, publishing the
pair reference and stopping the old branch. No isolated timing for those restore
substeps was recorded, so none is inferred here.

Recovery/regression tests were running concurrently during restore; filesystem
cache state was uncontrolled. These are single observations, not a benchmark or
latency guarantee. All cache hits still verify complete cached bytes, and source
capture interrupts PostgreSQL. No unique physical APFS savings were measured.

## Recovery and preservation evidence

The [paired test receipt](recovery/test-results.json) records 39 coordinator calls,
including nine deliberate process exits at three capture boundaries, four restore boundaries, and two Rust database
preparation boundaries. Application commands refuse access while a journal is
pending. Recovery keeps the old pair before reference publication and the new
pair after publication. Further checks cover dirty/untracked files, preservation
of abandoned source work, source corruption/link refusal, lock contention,
inherited child locks after coordinator death, running-limit refusal, source and
sibling isolation, and cleanup. These tests use deterministic mutations; they are
separate from the actual agent experiment. [Success receipt](recovery/SUCCESS).

The existing [lifecycle regression receipt](lifecycle/test-results.json) also
passed (84 calls, six injected exits), including database reset/import recovery and committed-data survival after
PostgreSQL immediate shutdown. [Success receipt](lifecycle/SUCCESS).
Rust formatting, five unit tests, Clippy with warnings denied and release builds
passed. See [validation receipt](validation.txt). These tests do not simulate
host power loss or establish protection against disk loss.

The [demo cleanup catalog](real/cleanup.json) and
[test cleanup catalog](recovery/cleanup.json) both show zero active clusters.
Their harnesses also asserted absence of PostgreSQL pidfiles across all owned
generations. The lifecycle harness stopped its owned servers. The initial sandbox
probe failed inside `initdb` before creating a running server. All database files,
old source generations, saved states and failed work are retained under the
ignored `benchmark-data/` directories for inspection; no garbage collection was
performed. No unrelated user files were reset, cleaned, committed or published.

## Reproduce and inspect

See [the current reproduction commands](../../docs/benchmarks.md#coding-agent-rewind-experiment) for a fresh
real agent run; see [contributing](../../CONTRIBUTING.md) for interruption tests. The existing recorded workspace is
`benchmark-data/paired-real/workspace`; its selected pair is the restored baseline.
Running this starts the owned database, prints the restored app and parks it again:

```sh
forklift pair benchmark-data/paired-real/workspace run -- python3 app.py
forklift pair benchmark-data/paired-real/workspace stop
```

Raw observations: [baseline](real/baseline.json), [modified](real/modified.json),
[restored](real/restored.json). Saved pairs are `baseline` and `modified`; restoring
one creates another retained source/database generation. All controlled writes
must pass through the coordinator gate. The wrapper is not suitable for arbitrary
background applications without an explicit pause adapter. External APIs, email,
arbitrary filesystem effects, process memory and other workspaces are excluded.

This run measures the bounded prototype. Current behavior and supported cleanup
are documented in the [CLI reference](../../docs/cli.md#paired-application-workspaces).
