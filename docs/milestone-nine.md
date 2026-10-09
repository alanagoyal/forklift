# Milestone nine: real parallel coding experiments

## Partial payments: the same task, two designs

Two real Codex agents received the same feature request: support partial payments.
They started from identical source, schema and invoice rows in separate Git
worktrees and Forklift database branches. Agent A added `invoices.amount_paid`
(an integer number of cents); agent B created a `payments` table and derived
balances from individual payments. Both migrated the already-paid invoice
correctly and independently verified their implementations.

After both finished, the harness used B's new application command to record a
2,500-cent payment against Ada Studio's 12,500-cent invoice. B reported a
10,000-cent balance and 15,000 cents outstanding overall. Then Forklift restored
A's baseline: exact selected source bytes/modes, schema, rows and application
output returned to version 1 and 17,500 cents outstanding. B's modified source,
schema, rows, payment and output remained exactly unchanged, as did the seed,
Git index/HEAD and unselected local files. All owned PostgreSQL servers stopped.

This is a measured local experiment, not a claim of automatic agent isolation:
Git worktrees separate files, Forklift branches separate databases, and paired
restore rewinds the chosen worktree's code and database together. Agents were
finished before capture and restore. The harness did not implement their feature.
See the [partial-payments evidence report](../results/2026-10-09-partial-payments-real/report.md)
for authentic transcripts, comparisons, limitations and retained setup failures.

```sh
cargo build --locked --release
python3 scripts/demo_partial_payments.py all benchmark-data/my-payments-run results/my-payments-run
```

Both paths must be new. The same prerequisites and `prepare`, `agents`, `finish`,
and `cleanup` phases described below apply to this script. `finish` records the
payment once; use a fresh run for a complete repeat.

## Original archive experiment

Two real Codex agents tried different implementations of invoice archiving at the
same time. Each used a Git worktree and a Forklift database branch created from the
same seed. One added an `archived` flag; the other moved the paid invoice into an
`invoice_archive` table. Both edited application code, applied a real transaction
that changed schema and rows, and ran the application successfully.

After both agents finished, Forklift saved their experiments and restored the
flag-based worktree to its baseline. Its selected source bytes/modes, full dumped
schema, rows and application output matched the baseline. The other worktree's
source, schema, rows and output stayed unchanged. The original seed also stayed
unchanged. Both design snapshots remain available, including the rejected design.

This is new evidence for the existing Rust project adapter, not a new coordination
engine. The [evidence report](../results/2026-10-09-parallel/report.md) includes
transcripts, timestamped provider events, before/after observations and cleanup.
It distinguishes the tested Git worktrees and hook execution from BB-native
worktree provisioning, which was not exercised here.

## Reproduce

Requirements: macOS/APFS, the project's Rust toolchain, Python 3, PostgreSQL 17 and
an authenticated `codex` CLI. The agents need network/provider access and permission
to connect to the owned local PostgreSQL Unix sockets. The run owns its fixture,
store and worktrees; it does not attach to an existing application's database.

```sh
cargo build --locked --release
python3 scripts/demo_parallel.py all benchmark-data/my-parallel-run results/my-parallel-run
```

Both paths must be new. `FORKLIFT_BIN` and `PG_BIN` override the default release
binary and Homebrew PostgreSQL 17 directory. The script can also run in phases:

```sh
python3 scripts/demo_parallel.py prepare benchmark-data/my-parallel-run results/my-parallel-run
python3 scripts/demo_parallel.py agents benchmark-data/my-parallel-run results/my-parallel-run
python3 scripts/demo_parallel.py finish benchmark-data/my-parallel-run results/my-parallel-run
```

`prepare` creates a private fixture repository, two Git worktrees and isolated
branches. It executes the tracked BB setup hook in each worktree with BB variables
removed, and captures the two baselines. `agents` runs two real Codex processes
through `forklift project run`. Each process inherits its worktree's database URL
and holds that worktree's command gate. The second launch waits for the first
provider's session event so the short initial store-admission operations do not
collide. Timestamped provider events must still prove overlapping actual turns.

`finish` requires authentic code-edit and successful migration receipts, inspects
the resulting databases independently, captures both designs, restores one, and
compares the other and the seed. It checks the Git index/HEAD and an unselected
local file, then executes teardown hooks and verifies no owned servers remain.
No harness code implements either archiving feature. The harness records every
CLI/SQL observation, preserves both agent transcripts, and does not overwrite them
on an attempted second agent invocation. Use new paths for a fresh attempt.

Normal errors attempt owned-server cleanup; timed-out agents are killed as process
groups before cleanup. After an externally killed run, first stop any still-running
owned agent processes, then run:

```sh
python3 scripts/demo_parallel.py cleanup benchmark-data/my-parallel-run results/my-parallel-run
```

## Scope

This remains a three-invoice command-line application, not a production web app or
performance benchmark. PostgreSQL durability settings stay enabled. These are
cooperating agents, not a security boundary against an agent deliberately connecting
to another branch. Agents were finished and app writers stopped before capture and
rewind; this does not add automatic BB-agent pausing or background-service control.
External side effects, merges and garbage collection remain outside the contract.
No new interruption suite was needed because this milestone changes only demo
orchestration and documentation; milestone eight covers interrupted project restore.
