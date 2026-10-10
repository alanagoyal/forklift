#!/usr/bin/env python3
import json,subprocess,re
from pathlib import Path
import imageio_ffmpeg
ROOT=Path(__file__).resolve().parent
ff=imageio_ffmpeg.get_ffmpeg_exe();src=ROOT/'exports/live-terminal.webm'
def run(args):subprocess.run([ff,'-hide_banner','-loglevel','error','-y',*map(str,args)],check=True)
common=['-an','-c:v','libx264','-preset','medium','-crf','18','-pix_fmt','yuv420p','-movflags','+faststart']
full=ROOT/'exports/forklift-terminal.mp4'
run(['-i',src,'-vf','fps=30',*common,full])
commands=json.loads((ROOT/'evidence/terminal-commands.json').read_text())
events=[json.loads(line) for name in ['soft','move'] for line in (ROOT/'recording'/f'{name}.jsonl').read_text().splitlines()]
origin=min(e['time'] for e in events);agents=[c for c in commands if 'codex' in c['argv']]
a=min(c['start'] for c in agents)-origin;b=max(c['end'] for c in agents)-origin
assert b>a>=0
# Omit startup/prompt wall and shorten the idle hold before restore.
# Keep the live agent output, both database inspections, and full restore.
segments=[(20,b,3),(b,51.5,1),(54.0,67.0,1)]
f='[0:v]split=3[s0][s1][s2];'+''.join(
    f'[s{i}]trim=start={start}:end={end},setpts=(PTS-STARTPTS)/{speed}[p{i}];'
    for i,(start,end,speed) in enumerate(segments)
)+'[p0][p1][p2]concat=n=3:v=1:a=0,fps=30[out]'
run(['-i',src,'-filter_complex',f,'-map','[out]',*common,ROOT/'exports/forklift-terminal-fast.mp4'])
run(['-sseof','-5','-i',full,'-frames:v','1',ROOT/'exports/poster.jpg'])
run(['-ss',str(a+15),'-i',full,'-frames:v','1',ROOT/'exports/agent-frame.jpg'])
for name in ['forklift-terminal.mp4','forklift-terminal-fast.mp4']:
    out=subprocess.run([ff,'-hide_banner','-i',str(ROOT/'exports'/name)],capture_output=True,text=True).stderr
    assert '1920x1080' in out and ('yuv420p' in out or 'yuvj420p' in out) and 'h264' in out,out
    info={'file':name,'bytes':(ROOT/'exports'/name).stat().st_size,'duration':re.search(r'Duration: ([\d:.]+)',out).group(1)}
    print(json.dumps(info))
(ROOT/'exports/edit.json').write_text(json.dumps({'source':'actual live PTY terminal capture','segments_source_seconds_and_speed':segments,'on_screen_speed_label':False,'agent_speed_factor':3,'rest_of_video_speed_factor':1,'synthetic_terminal_output':False,'audio':'none'},indent=2))
