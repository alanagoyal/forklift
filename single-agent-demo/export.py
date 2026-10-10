#!/usr/bin/env python3
import json,subprocess,re
from pathlib import Path
import imageio_ffmpeg
ROOT=Path(__file__).resolve().parent
ff=imageio_ffmpeg.get_ffmpeg_exe();src=ROOT/'exports/live-terminal.webm'
def run(args):subprocess.run([ff,'-hide_banner','-loglevel','error','-y',*map(str,args)],check=True)
commands=json.loads((ROOT/'evidence-v2/terminal-commands.json').read_text())
events=[json.loads(line) for line in (ROOT/'recording/soft.jsonl').read_text().splitlines()]
origin=events[0]['time']
scenes={c['scene']:c['time']-origin for c in commands if 'scene' in c}
agent=next(c for c in commands if 'codex' in c.get('argv',[]))
a=agent['start']-origin;b=agent['end']-origin
restore=next(v for k,v in scenes.items() if k.startswith('rewind with'))
# Preserve all commands and results; accelerate only the agent's work.
filters=f'[0:v]split=3[s0][s1][s2];[s0]trim=end={a},setpts=PTS-STARTPTS[p0];[s1]trim=start={a}:end={b},setpts=(PTS-STARTPTS)/3[p1];[s2]trim=start={b},setpts=PTS-STARTPTS[p2];[p0][p1][p2]concat=n=3:v=1:a=0,fps=30[out]'
common=['-an','-c:v','libx264','-preset','medium','-crf','18','-pix_fmt','yuv420p','-movflags','+faststart']
run(['-i',src,'-filter_complex',filters,'-map','[out]',*common,ROOT/'exports/forklift-blog.mp4'])
run(['-i',src,'-vf',f'trim=start={restore},setpts=PTS-STARTPTS,fps=30',*common,ROOT/'exports/forklift-twitter.mp4'])
run(['-i',ROOT/'exports/forklift-twitter.mp4','-ss','12','-frames:v','1',ROOT/'exports/poster.jpg'])
for key,t in scenes.items():
    if key.startswith('try undoing'):
        run(['-ss',str(t+4),'-i',src,'-frames:v','1',ROOT/'exports/code-only.jpg'])
for name in ['forklift-blog.mp4','forklift-twitter.mp4']:
    out=subprocess.run([ff,'-hide_banner','-i',str(ROOT/'exports'/name)],capture_output=True,text=True).stderr
    assert '1920x1080' in out and 'h264' in out,out
    print(name,re.search(r'Duration: ([\d:.]+)',out).group(1))
(ROOT/'exports/edit.json').write_text(json.dumps({'source':'live PTY capture','blog_agent_speed':3,'twitter_speed':1,'twitter_source_start':restore,'scenes':scenes,'no_synthetic_output':True},indent=2))
