# Two real agents, two designs, one independent rewind

Observed on October 9, 2026, using the native Rust `forklift project` adapter from
revision `ab03943` and the new [demo harness](../../scripts/demo_parallel.py).
The fixture is the existing three-invoice Python command-line application.

## What actually happened

Two authentic Codex sessions ran concurrently in separate real Git worktrees.
Both started from the same PostgreSQL seed and version-one source. Each edited
`src/app.py`, created and executed `migrations/archive.sql`, and verified the app.
The harness did not write either feature implementation.

| State | Soft-archive worktree | Separate-table worktree | Original seed |
|---|---|---|---|
| Before agents | App lists 1, 2, 3; original schema | App lists 1, 2, 3; original schema | Original schema and rows |
| After agents | App lists 1, 2; all 3 rows remain in `invoices`, with invoice 3 flagged `archived` | App lists 1, 2; invoice 3 moved to `invoice_archive` | Unchanged |
| After rewinding soft archive | App lists 1, 2, 3; original source, schema and rows restored | Exact experiment source, schema, rows and app output unchanged | Unchanged |

Both version-two apps reported `archived_count: 1`; all app observations reported
`outstanding_cents: 17500`. Matching visible output did not imply matching storage:
the agents implemented different schema and row changes.

The soft agent executed `BEGIN / ALTER TABLE / UPDATE 1 / COMMIT`. The move agent
executed `BEGIN / CREATE TABLE / DELETE 1 / COMMIT`; its INSERT is inside a
writable CTE whose final DELETE supplies the command tag. Its SQL and observed
archive row verify the move. Both agent invocations exited successfully on their
first attempt, with no sandbox/socket retry.

Provider `turn.started`/`turn.completed` events overlapped by about **27.15 seconds**.
These are approximate event-receipt times recorded by the parent process, proving
concurrent turns, not simultaneous CPU execution or a speed/throughput result.
[Event timestamps](agent-event-times.json), [provider intervals](provider-overlap.json)
and [process intervals](agent-intervals.json) remain available.

## Isolation and recovery checks

[Identities](identities.json) record distinct Git administrative workspace paths and
PostgreSQL branch names. Both worktrees used the same named `development` seed.
After both agents finished, the harness captured each as `experiment` and restored
only the soft-archive worktree's `baseline`.

Independent readers compared selected source SHA-256 hashes and file modes, the
normalized `pg_dump --schema-only --no-owner --no-privileges` output, every public table's ordered rows,
and parsed application output. Only pg_dump's random `\restrict`/`\unrestrict`
nonce lines were removed from schema comparisons. The restored observation equals
its complete baseline; the sibling observation equals its complete modified state.
The sibling's selected branch binding also remained unchanged. The seed's source,
full schema, rows and app output match its control observation.

The worktree path stayed stable. Git HEAD, staging index, common-directory pointer
and `.git` pointer hashes matched their pre-agent values. Selected dirty and
untracked baseline files were restored, executable mode was preserved, and the
unselected `.env.local` fixture file remained unchanged. No secrets were used in
that fixture file. The rejected soft-archive design remains a named saved pair;
its retained source inventory matches its saved manifest. No checkpoint was deleted.

Both BB setup hooks and teardown hooks executed successfully with `BB_*` and
`NODE_ENV` stripped. **The harness used `git worktree add`, not BB's provisioning
service.** This verifies the real hook entrypoints and worktree adapter, not the
entire BB creation/archive lifecycle.

## Evidence

- Authentic prompts, invocation arguments and streams: [soft transcript](soft-agent.jsonl),
  [move transcript](move-agent.jsonl), adjacent `*-prompt.txt`, `*-agent-command.json`
  and `*-agent.stderr` files.
- Agent-written source/migrations: [soft design](soft-agent-source/src/app.py),
  [soft migration](soft-agent-source/migrations/archive.sql),
  [move design](move-agent-source/src/app.py), [move migration](move-agent-source/migrations/archive.sql).
- Full observations: [soft baseline](soft-baseline.json), [soft modified](soft-modified.json),
  [soft restored](soft-restored.json), [move baseline](move-baseline.json),
  [move modified](move-modified.json), [move after sibling rewind](move-after-sibling-rewind.json),
  [control before](control-before.json), [control after](control-after.json).
- [Summary](summary.json), [retained experiment](retained-experiment.json),
  [recorded commands](commands.jsonl), [environment](environment.txt) and [cleanup](cleanup.json).

The first verification attempt incorrectly demanded a separate `INSERT 0 1` tag
from the move agent's valid CTE migration. It stopped with an assertion and cleaned
up the servers. The verifier was corrected to check the actual executed migration,
transaction tags and independently observed rows. Reverification passed without
rerunning either agent or changing its feature code. The existing soft experiment
checkpoint was verified and reused. [Attempt history](verification-attempts.json)
records this harness-only false negative; no Rust engine fix was needed.

## Limits and final state

`fsync`, `full_page_writes`, `synchronous_commit` and data checksums were all on in
every observation. Final cleanup records **zero active clusters** and the harness
also found no owned `postmaster.pid` files. Raw clusters/worktrees remain under
ignored `benchmark-data/parallel-2026-10-09`; the compact evidence is retained here.

This tiny CLI fixture establishes one real parallel workflow, not workload scale,
performance, physical-space savings, automatic coordination of active agents, or
security isolation against malicious agents. Both agents had finished before
rewind; unmanaged writers still must cooperate. External effects and database
merging are not handled. There was no host power-loss test in this experiment.
See [reproduction and workflow](../../docs/benchmarks.md#parallel-worktree-experiments).
