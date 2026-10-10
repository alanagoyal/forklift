# code-only revert video

A 30-second, silent, 1080×1080 H.264 video for social posts. One Codex agent
adds partial payments (new `app.py` plus a migration that drops `invoices.paid`).
Reverting only the code with `git checkout -- app.py` makes the app fail with
`column "paid" does not exist`; `forklift project restore baseline` rewinds code
and database together and the app works again.

Evidence: `../results/2026-10-09-code-only-revert/`, produced by
`scripts/demo_code_only_revert.py` (a real `codex exec` run; prompt in
`agent-prompt.txt`). The renderer asserts the evidence matches what the video
shows before encoding.

Presentation notes: terminal output is condensed (the agent's JSONL, restore's
JSON receipt and the full traceback are summarized). `python3 app.py` is shown
for brevity; it ran as `forklift project run -- python3 app.py`, which only
injects `DATABASE_URL`.

Re-render from the repository root:
`/tmp/forklift-video-tools/bin/python code-revert-video/render.py`
(`--frames` writes review stills only).
