# single-agent terminal demo

A fresh real Codex run changes application code, renames `invoices.customer` to `client_name`, and deletes paid invoice 3. This deliberately differs from the earlier blog's additive archive example: old code really fails against the renamed column. Do not attribute this error to that earlier experiment.

The recording shows baseline output and capture, actual agent work, successful modified app output, `git restore src/app.py`, the actual Postgres missing-column error, then puts the actual agent-written code back before `forklift project restore before_demo`. Independent observations compare restored source hashes/modes, normalized schema, rows, app JSON, and enabled durability settings against baseline. The original seed is also checked unchanged.

`show.py` formats actual app JSON and prints the database table. On failure it prints the original SQL error lines and preserves the failed exit status, omitting Python traceback noise. These display helpers live outside checkpointed source paths. Terminal comments are lowercase; raw SQL, Postgres errors, and agent output retain their original case.

`server.py` runs real PTYs and streams their bytes into a single xterm.js browser terminal. Canvas capture records the actual terminal display, not simulated output. Raw bytes and command/scene timestamps are retained in `recording/` and `evidence-v2/`.

`export.py` creates the blog cut (agent work at 3×, other steps normal speed) and the Twitter cut (only the paired restore and verification, normal speed). No speed labels are burned into the footage. MP4s and source WebM are ignored by Git. The original parallel recording remains in `../terminal-demo/`.

The first preparation hit sandbox shared-memory restrictions; its receipts remain in `evidence/`. The first recording stopped before any agent ran because a hyphenated checkpoint name was invalid; `recording/setup-name-error.jsonl` preserves that attempt. The first completed take exposed noisy CLI JSON output. The second take redirects the real capture and restore receipts to files and uses `before_demo`; first-take output is retained.

This runner is bound to a disposable fixture and has a one-shot localhost-only start endpoint. Viewing `watch.html` never starts an agent or database. For a new run, prepare fresh fixture/evidence paths and update the runner. xterm.js assets and licenses are shared from `../terminal-demo/vendor/`.
