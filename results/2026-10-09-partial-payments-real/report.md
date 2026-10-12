# Application rewind: recorded partial-payments example

Recorded on 2026-10-09 with PostgreSQL 17.11 on macOS/APFS. This three-invoice
fixture demonstrates one isolated code/database rewind, not performance or a
production payments system. Two coding agents used separate Git worktrees and
Forklift branches. Both had finished before capture and restore.

| Observation | Before changes | Before rewind | After rewinding A |
| --- | --- | --- | --- |
| A application | Version 1, $175 outstanding | Version 2, $175 outstanding | Exact version 1 baseline, $175 outstanding |
| A schema | Original invoices table | Added integer `amount_paid` column | Original schema |
| B application | Version 1, $175 outstanding | Version 2, $150 outstanding after a $25 payment | Exact modified state preserved |
| B schema | Original invoices table | Separate `payments` table | Modified schema preserved |

[verification.json](verification.json) preserves the recorded source hashes/modes,
normalized schema dumps, every public table's rows and application output before
and after rewind. A's baseline equals its restored observation; B's modified
observation equals its observation after A's rewind; the seed is unchanged.
Every retained observation reports `fsync`, `full_page_writes`, `synchronous_commit`
and data checksums enabled. The cleanup receipt recorded zero active clusters.
These files were compacted from the existing run; no experiment was rerun.

Selected source was `src`, `migrations` and `notes.txt`, including preexisting
untracked/dirty files. Schema normalization removes only pg_dump's random
`\restrict`/`\unrestrict` tokens. Source hashes establish inventory equality;
they do not reproduce the agent's implementation. Transcripts, copied application
source, retry logs and per-command receipts are omitted from the current tree.
The original artifacts remain at the
[recorded repository revision](https://github.com/alanagoyal/forklift/tree/671f166/results/2026-10-09-partial-payments-real).

The harness used real Git worktrees and tracked setup/teardown hooks; BB-native
provisioning was not exercised. This example does not establish automatic agent
pausing, malicious-agent isolation, external-side-effect rollback, database merging
or host power-loss recovery. The recorded code revision was not identified; this
is evidence of that fixture's outcome, not validation of the current release.

Reproduce with new paths and an authenticated Codex CLI:

```sh
cargo build --locked --release
python3 scripts/demo_partial_payments.py all benchmark-data/new-payments-run benchmark-data/new-payments-evidence
```

See [reproduction and recovery](../../docs/benchmarks.md#parallel-worktree-experiments).
`finish` records one payment and is not an idempotent replay command.
