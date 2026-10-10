# Demo and learning materials

These materials were created alongside the early Forklift experiments and are
preserved here with their source and recorded evidence. They describe the engine
at the time of each recording; they are not additional current compatibility or
performance claims.

- [`code-revert-video`](../../code-revert-video/README.md) is the final code-only
  Git revert versus paired restore video. Its real-agent driver is
  [`demo_code_only_revert.py`](../../scripts/demo_code_only_revert.py), and its
  [recorded results](../../results/2026-10-09-code-only-revert/summary.json) verify
  that code-only revert fails while Forklift restores the starting output.
- [`partial-payments-video`](../../partial-payments-video/README.md) contains
  the earlier parallel-agent replay, render source, and exported review frames.
- `demo-video`, [`single-agent-demo`](../../single-agent-demo/README.md), and
  [`terminal-demo`](../../terminal-demo/README.md) retain earlier presentation
  experiments, terminal players, and evidence. Their ignored local exports can
  be regenerated; references to local benchmark directories are historical.
- [`learning-site`](../../learning-site/README.md) is the static field guide.
  Its bundled source snapshot is intentionally historical and records its own
  revision. It is not the current source of truth for Forklift behavior.

The 2026-10-10 repository audit checked Python and JavaScript syntax, JSON/JSONL
validity, and the final code-only revert's recorded before/after assertions. It did
not rerun the coding agents, re-record videos, or republish the field guide.
Local site deployment identity is excluded from Git.
