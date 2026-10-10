# real terminal demo

This records two actual Codex CLI processes in PTYs, streamed into side-by-side xterm.js panes. The canvas recorder captures the live terminal layers; it does not synthesize agent output. `watch.html` plays the exported recording. The separate `../demo-video/` directory preserves the earlier illustrative concept.

The fresh fixture is `benchmark-data/terminal-demo-2026-10-09/`, prepared with `scripts/demo_parallel.py prepare`. Each agent changes real code and executes a real migration in its own worktree/database. After both finish, only A is restored. The runner independently checks A against baseline, B against its modified state, and the seed against its original state. `evidence/` contains observations and command receipts; `recording/` contains timestamped raw PTY bytes. All owned PostgreSQL servers were stopped after recording (`evidence/cleanup.json`).

## artifacts

- `exports/live-terminal.webm`: original live capture.
- `exports/forklift-terminal.mp4`: full uncut capture, H.264, 1920×1080.
- `exports/forklift-terminal-fast.mp4`: edited capture: startup and some idle time removed, agent work accelerated 3×, no on-screen speed label. Rewind and checks remain normal speed.
- `exports/edit.json`: edit parameters; both videos are silent.

Exports are ignored by Git. `export.py` requires imageio-ffmpeg; this run used a temporary venv at `/tmp/forklift-video-tools`. xterm.js and its canvas addon are vendored with licenses.

`server.py` serves port 8789 and exposes a one-shot localhost-only start action. It is an experiment runner bound to this disposable fixture, not a general-purpose production server. Do not start it again against an already-used fixture expecting a fresh run: prepare fresh paths and update the runner first. Viewing `watch.html` does not launch commands or databases.
