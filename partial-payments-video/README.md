# partial-payments terminal video

A 76-second, silent, 1920×1080 H.264 terminal replay using the user-approved
storyboard, including the “basic invoicing app” introduction. Narration below the terminals
has no comment prefixes; terminal comments retain shell syntax. Uses Basecase’s
dark-mode palette and Geist/Geist Mono fonts, without corner labels or a progress bar.
Commands and results are presented as an editorial reconstruction, not raw PTY
footage. Agent implementation steps are condensed summaries, not simulated live
agent transcripts. The watch page identifies this as a replay.

Source evidence: `../results/2026-10-09-partial-payments-real/`. The renderer
asserts the saved baseline/restored and modified/preserved observations match
before encoding. Both agents had finished before capture/restore in that test.

- `render.py`: Pillow/ffmpeg renderer; requires pillow and imageio-ffmpeg.
- `storyboard.json`: editable timed text generated from render.py.
- `exports/forklift-partial-payments-v3.mp4`: final video.
- `exports/frame-*.png`: representative frames for visual review.
- `exports/metadata.json`: duration, format and provenance.
- `index.html`: watch/download page.

Re-render: `/tmp/forklift-video-tools/bin/python partial-payments-video/render.py`
from the repository root. Edit text/timing in render.py before re-rendering.
