#!/usr/bin/env python3
"""Render an editorial terminal replay of the verified partial-payments run."""
from pathlib import Path
import json, subprocess, textwrap
from PIL import Image, ImageDraw, ImageFont
import imageio_ffmpeg
ROOT=Path(__file__).resolve().parent
E=ROOT.parent/'results/2026-10-09-partial-payments-real'
s=json.loads((E/'summary.json').read_text())
assert s['exact_code_schema_rows_app_restored'] and s['sibling_unchanged']=='ledger'
assert s['baseline']['outstanding_cents']==17500
assert s['ledger_after_payment_and_sibling_rewind']['outstanding_cents']==15000
assert json.loads((E/'amount-baseline.json').read_text())==json.loads((E/'amount-restored.json').read_text())
assert json.loads((E/'ledger-modified.json').read_text())==json.loads((E/'ledger-after-sibling-rewind.json').read_text())
W,H,FPS,DURATION=1920,1080,24,76
C={'bg':'#0a0a0a','panel':'#111110','line':'#272725','text':'#e9e7e2','muted':'#b4b1aa','a':'#ff5b2e','b':'#ff5b2e','comment':'#b4b1aa','dim':'#6d6a64'}
FONT=str(ROOT/'fonts/GeistMonoVF.ttf')
fonts={n:ImageFont.truetype(FONT,n) for n in [19,21,23,25,27,30]}
narration_font=ImageFont.truetype(FONT,27)
check_font=ImageFont.truetype('/System/Library/Fonts/Menlo.ttc',27)
def line(text,kind='out',at=0): return {'text':text,'kind':kind,'at':at}
def lines(items): return [line(*i) if isinstance(i,tuple) else line(i) for i in items]
baseA=lines(['Worktree: amount','Database: isolated A','Starting outstanding: $175'])
baseB=lines(['Worktree: ledger','Database: isolated B','Starting outstanding: $175'])
cap=lines([('$ forklift project capture baseline','cmd',.5),('Baseline saved: code + database.','ok',1.8)])
implA=lines([('# Store the amount paid on each invoice.','comment',.2),('Edited app + applied migration','out',2),('+ verified balances.','out',2.4),('Added invoices.amount_paid.','ok',3.3),('Outstanding: $175','out',4)])
implB=lines([('# Store individual payments','comment',.2),('# in a separate table.','comment',.5),('Edited app + applied migration','out',2),('+ verified balances.','out',2.4),('Created payments table.','ok',3.3),('Outstanding: $175','out',4)])
payment=lines([('$ forklift project run -- python3 src/app.py \\','cmd',.3),('    record-payment 1 2500','cmd',1.4),('','out',2),('Ada Studio invoice:   $125','out',2.4),('Payment recorded:      $25','ok',3),('Remaining balance:    $100','out',3.6),('Total outstanding:    $150','ok',4.2)])
save=lines([('$ forklift project capture experiment','cmd',.5),('Experiment saved: code + database.','ok',1.8)])
restore=lines([('$ forklift project restore baseline','cmd',.3),('$ forklift project run -- python3 src/app.py','cmd',2),('','out',3),('Original application restored.','ok',3.3),('Original schema restored.','ok',3.8),('amount_paid column gone.','out',4.3),('Original rows restored.','ok',4.8),('Total outstanding: $175','out',5.4)])
preserved=lines([('$ forklift project run -- python3 src/app.py','cmd',.4),('','out',1.4),('Partial-payments application intact.','ok',2),('Payments table intact.','ok',2.6),('Ada Studio’s $25 payment intact.','ok',3.2),('Total outstanding: $150','out',3.8)])
phases=[
 {'t':0,'end':9,'comment':['# I asked two Codex agents to “support partial payments” in a basic invoicing app.','# They worked in parallel in two configured Git worktrees, each with its own','# Forklift database branch from the same starting state.'],'a':baseA,'b':baseB},
 {'t':9,'end':16,'comment':['# Save the same starting state in each worktree.'],'a':baseA+lines([''])+cap,'b':baseB+lines([''])+cap},
 {'t':16,'end':29,'comment':['# Agents work in parallel'],'a':implA,'b':implB},
 {'t':29,'end':40,'comment':['# Make changes to B’s database'],'a':lines(['Added invoices.amount_paid.','Outstanding: $175','','No changes in this worktree.']),'b':payment},
 {'t':40,'end':47,'comment':['# Save both experiments'],'a':save+lines([('Outstanding: $175','out',2.6)]),'b':save+lines([('Ada Studio’s $25 payment saved.','out',2.6),('Outstanding: $150','out',3.1)])},
 {'t':47,'end':58,'comment':['# Rewind A’s approach'],'a':restore,'b':lines(['Partial-payments application.','Payments table.','Ada Studio’s $25 payment.','Outstanding: $150','','No changes in this worktree.'])},
 {'t':58,'end':67,'comment':['# B keeps its progress'],'a':lines(['Original application restored.','Original schema and rows restored.','Outstanding: $175']),'b':preserved},
 {'t':67,'end':76,'comment':['# VERIFIED'],'a':lines([('✓ Code matches baseline.','ok',.2),('✓ Schema matches baseline.','ok',.7),('✓ Rows match baseline.','ok',1.2),('✓ Output matches baseline.','ok',1.7),('','out',2),('Total outstanding: $175','out',2.3)]),'b':lines([('✓ Code matches saved experiment.','ok',.2),('✓ Schema matches saved experiment.','ok',.7),('✓ Rows match saved experiment.','ok',1.2),('✓ Output matches saved experiment.','ok',1.7),('','out',2),('Total outstanding: $150','out',2.3)])}
]
for phase in phases:
 phase['comment']=[c.removeprefix('# ') for c in phase['comment']]
phases[-1]['comment']=['VERIFIED', '✓ A’s code, schema, rows, and output match its baseline.', '✓ B’s code, schema, rows, and output match its saved experiment.', '✓ Original seed unchanged.']
phases[-1]['a']=lines(['Original application restored.', 'Original schema and rows restored.', 'Total outstanding: $175'])
phases[-1]['b']=lines(['Partial-payments application intact.', 'Payments table intact.', 'Ada Studio’s $25 payment intact.', 'Total outstanding: $150'])
(ROOT/'storyboard.json').write_text(json.dumps(phases,ensure_ascii=False,indent=2)+'\n')
def frame(t):
 p=next(p for p in phases if p['t']<=t<p['end']); dt=t-p['t']
 im=Image.new('RGB',(W,H),C['bg']); d=ImageDraw.Draw(im)
 def text(x,y,s,size=25,color='text'):d.text((x,y),s,font=fonts[size],fill=C.get(color,color))
 for i,c in enumerate(p['comment']):
  assert d.textlength(c,font=narration_font)<1792,c
  color=C['text'] if i==0 else C['muted']
  if c.startswith('✓ '):
   d.text((64,855+i*42),'✓',font=check_font,fill=color)
   d.text((96,855+i*42),c[2:],font=narration_font,fill=color)
  else:
   d.text((64,855+i*42),c,font=narration_font,fill=color)
 for name,x in [('a',64),('b',984)]:
  d.rounded_rectangle((x,64,x+872,810),radius=14,fill=C['panel'],outline=C['line'],width=2)
  for j,c in enumerate([C['dim']]*3):d.ellipse((x+24+j*25,85,x+36+j*25,97),fill=c)
  text(x+121,82,'AGENT '+name.upper(),21,'text')
  text(x+602,84,'~/'+('amount' if name=='a' else 'ledger'),19,'muted')
  d.line((x,118,x+872,118),fill=C['line'],width=2)
  y=147
  for l in p[name]:
   if dt<l['at']:break
   st=l['text'];kind=l['kind'];color={'cmd':name,'ok':name,'comment':'comment'}.get(kind,'text')
   if kind=='cmd':
    count=int((dt-l['at'])*85);st=st[:count]+('▌' if count<len(st) else '')
   assert d.textlength(l['text'],font=fonts[23])<817,l['text']
   text(x+28,y,st,23,color);y+=40
 return im
if __name__=='__main__':
 for t in [3,21,35,53,63,72]:frame(t).save(ROOT/'exports'/f'frame-{t:02d}.png')
 frame(72).save(ROOT/'exports/poster-v3.jpg',quality=95)
 ff=imageio_ffmpeg.get_ffmpeg_exe()
 cmd=[ff,'-hide_banner','-loglevel','error','-y','-f','rawvideo','-vcodec','rawvideo','-pix_fmt','rgb24','-s',f'{W}x{H}','-r',str(FPS),'-i','-','-an','-c:v','libx264','-preset','fast','-crf','18','-pix_fmt','yuv420p','-movflags','+faststart',str(ROOT/'exports/forklift-partial-payments-v3.mp4')]
 proc=subprocess.Popen(cmd,stdin=subprocess.PIPE)
 for i in range(FPS*DURATION):
  proc.stdin.write(frame(i/FPS).tobytes())
  if i%(FPS*10)==0:print(f'Rendered {i/FPS:.0f}s / {DURATION}s',flush=True)
 proc.stdin.close();assert proc.wait()==0
 (ROOT/'exports/metadata.json').write_text(json.dumps({'duration_seconds':DURATION,'fps':FPS,'width':W,'height':H,'audio':False,'presentation':'scripted terminal replay; summaries of verified results, not raw PTY footage','evidence':str(E)},indent=2)+'\n')
 print('Video complete.',flush=True)
