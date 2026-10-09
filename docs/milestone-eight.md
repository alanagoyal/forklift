# Milestone eight: configurable Git worktree adapter

Implemented `forklift project` in Rust, for projects that can run against the local
PostgreSQL 17 cluster and Unix-socket connection URL. Configuration selects owned
source paths and argv commands; application/framework names never enter the engine.

The stable Git worktree path is the key difference from `forklift pair`, whose
owned source path changes between generations. The project journal extends paired
recovery across in-place source replacement. Pre-rewind source is retained, managed
commands remain gated, and Git's index/history plus unselected files are preserved.
BB setup/teardown templates use Git identity and machine-local seed configuration.

See [setup and workflow](project.md). Validation results are recorded in
[the milestone evidence report](../results/2026-10-08-project/report.md).

Scope: explicit owned paths, cooperative synchronous commands, manual BB-agent pause,
no automatic dependency regeneration, no background-service supervisor, no nested
exclusions, no TCP-only drivers or hosted-service adapters, no GC or database merge.
The npm example is configuration guidance; integration fixtures use Python and bash
with real `psql`. Native BB provisioning itself is not exercised by the integration
test; tracked hooks are executed in real Git worktrees with BB variables stripped.
