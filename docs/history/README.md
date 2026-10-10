# Design history and measured results

These documents record Forklift's development and experiments in October 2026.
They preserve the original reasoning, validation and measurements. Commands,
limitations, proposed behavior and statements about release status describe each
recorded stage; they are not the current usage guide or a maintained roadmap.

Start with the [README](../../README.md) to install and try Forklift. Use the
[project guide](../project.md), [database CLI guide](../lifecycle.md),
[paired restore guide](../paired.md), and [checkpoint format](../checkpoint-format.md)
for current behavior. [CONTRIBUTING.md](../../CONTRIBUTING.md) describes the current
test suite. The [architecture decision](../architecture-decision.md) explains the
original storage choice and distinguishes that decision from later implementation.

## Original plan

The [archived project plan](project-plan.md) contains the initial scope, proposed
commands and acceptance gates, followed by updates as milestones were completed.
Its future work describes possibilities considered during development, rather than
commitments to implement them.

## Milestone reports

| Report | Scope |
| --- | --- |
| [1. Feasibility](milestone-one.md) | Stopped PostgreSQL copies and APFS clones; correctness and timings. |
| [2. Branch lifecycle](milestone-two.md) | CLI, process ownership, durable catalog and interruption recovery. |
| [3. Immutable checkpoints](milestone-three.md) | Object packs, integrity checks, export and restore. |
| [4. Many stored branches](milestone-four.md) | Verified cache, 100 stored branches and separately measured active servers. |
| [5. Reproducible demo](milestone-five.md) | Index, schema and row experiments; reset and independent restore. |
| [6. Paired rewind prototype](milestone-six.md) | Python coordination of selected source and database restore. |
| [7. Rust paired rewind](milestone-seven.md) | Native CLI implementation and compatibility validation. |
| [8. Git worktree adapter](milestone-eight.md) | Project configuration, command gate and interrupted restore recovery. |
| [9. Parallel coding experiments](milestone-nine.md) | Two agents change separate worktrees/databases; one experiment is rewound. |

Reports link to retained evidence in [results](../../results/), including the
[partial-payments experiment](../../results/2026-10-09-partial-payments-real/report.md).
Measurements apply to the recorded revisions, machines and workloads. Exact unique
APFS storage savings and performance advantages over Neon or DBLab remain unmeasured.
