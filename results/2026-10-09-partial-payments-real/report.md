# Partial payments: real parallel agents and one isolated rewind

Measured on October 9, 2026 using the existing Rust Forklift binary, PostgreSQL
17.11 and two genuine authenticated Codex CLI processes. This is a three-invoice
application experiment, not a performance benchmark or a production payments system.

Both agents received **support partial payments** and started with identical
selected source, full schema and rows. Separate Git worktrees isolated their files;
separate Forklift branches isolated their databases. The harness supplied prompts
and checked outcomes; it did not write feature code or execute feature migrations.

| Observation | Agent A (`amount`) | Agent B (`ledger`) |
| --- | --- | --- |
| Implementation | `invoices.amount_paid`, integer cents | `payments` table; balances derived from rows |
| After migration | $175 outstanding | $175 outstanding |
| Historical Cedar Works invoice | $90 paid, $0 balance | $90 payment row, $0 balance |
| New payment | None | $25 against Ada Studio's $125 invoice |
| Before rewind | Version 2, $175 outstanding | Version 2, Ada balance $100, total $150 |
| After rewinding A | Exact version 1 baseline, $175 outstanding | Exact modified state preserved, including $25 payment |

The incompatible schema choices need different application code. We did not force
or measure a shared-database collision. This test demonstrates that their separate
experiments can coexist and one can be discarded without discarding the other.

## Evidence

- [Prompts A](amount-prompt.txt) and [B](ledger-prompt.txt), authentic [A transcript](amount-agent.jsonl) and [B transcript](ledger-agent.jsonl), and exact agent launch command JSON files.
- [Provider overlap](provider-overlap.json): 55.139 seconds between observed provider turn events. These are parent-process receipt timestamps, not model execution timings or a speed claim.
- Both agents executed `psql --single-transaction -f migrations/partial_payments.sql` with `ON_ERROR_STOP`, migrated the real database and ran the app. They tested payment behavior using real SQL with rollback; their retained transcripts describe those checks.
- [Agent A source](amount-agent-source/src/app.py) and [migration](amount-agent-source/migrations/partial_payments.sql); [agent B source](ledger-agent-source/src/app.py) and [migration](ledger-agent-source/migrations/partial_payments.sql).
- [A baseline](amount-baseline.json) equals [A restored](amount-restored.json), including selected source hashes/modes, full normalized schema dump, every public table's rows and application output.
- [B after payment](ledger-modified.json) equals [B after A's rewind](ledger-after-sibling-rewind.json), including modified source, schema, payment rows and output. The [payment command output](ledger-payment-output.json) shows 2,500 cents paid, 10,000 cents remaining on Ada, and 15,000 cents outstanding overall.
- [Seed before](control-before.json) equals [seed after](control-after.json). The harness also compared each worktree's Git index, HEAD, `.git` pointer and `commondir` bytes and its unselected local file.
- [Summary](summary.json), [all CLI/SQL observations](commands.jsonl), [retained rejected experiment](retained-experiment.json), and [cleanup](cleanup.json). Cleanup reports zero active clusters and the harness checked that no owned `postmaster.pid` files remain.

All observations verified `fsync`, `full_page_writes`, `synchronous_commit` and
`data_checksums` were `on`. Exact schema comparison removes only pg_dump's randomly
generated `\restrict`/`\unrestrict` tokens. Selected source is `src`, `migrations`
and `notes.txt`, including preexisting untracked/dirty baseline files; this is not
a claim to rewind arbitrary files outside that configured selection.

The harness uses `git worktree add` and runs the tracked BB setup/teardown hooks
with BB variables removed. BB-native worktree provisioning was not exercised.
Agents completed before capture and restore. This does not add agent pausing,
a security boundary against deliberately connecting to a sibling database,
external-side-effect rollback, merging, or garbage collection.

Python compilation, archive/payment prompt dispatch, saved evidence comparisons,
`git diff --check` for code/docs/JSON, and all eight Rust tests passed; see
[validation](validation.json). Raw `.diff` artifacts retain their unified-diff
blank context prefixes, which Git flags as trailing spaces when checking the
artifact as newly added text.

## Attempts and reproduction

The [initial sandbox setup attempt](../2026-10-09-partial-payments/commands.jsonl)
failed at PostgreSQL shared-memory initialization before agents ran; its cleanup
is retained. The real run used authorized access to local PostgreSQL shared memory.
Both real agents completed successfully on their first invocation. The initial
receipt checker expected literal `BEGIN`/`COMMIT` output and stopped before recording
the payment because `psql --single-transaction` does not print those command tags.
The checker was corrected to accept that transaction wrapper, then `finish` ran
successfully against the unchanged agent implementations and databases. Agent
transcripts were neither replaced nor simulated.

```sh
cargo build --locked --release
python3 scripts/demo_partial_payments.py all benchmark-data/new-payments-run results/new-payments-run
```

Use new paths. Requires macOS/APFS, Python 3, PostgreSQL 17, authenticated Codex and
permission for local Unix sockets/shared memory. The same script supports
`prepare`, `agents`, `finish` and `cleanup`. `finish` records a payment once and is
not an idempotent replay command. The original archive experiment remains available
through `scripts/demo_parallel.py` and its separate evidence directory.
