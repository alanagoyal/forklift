const canvas = document.querySelector('canvas'), ctx=canvas.getContext('2d',{alpha:false});
const W=1920,H=1080, C={bg:'#111719',card:'#1b2326',panel:'#141c1f',line:'#364347',ink:'#f3f3e9',muted:'#a2b0b1',green:'#cef38b',blue:'#9dc9ff',yellow:'#ffe297'};
let DATA,mode='blog',time=0,playing=false,startAt=0,recording=false;
const clamp=(v)=>Math.min(1,Math.max(0,v));
const ease=(v)=>{v=clamp(v);return v*v*(3-2*v)};
function box(x,y,w,h,r=14,fill=C.card,stroke){ctx.beginPath();ctx.roundRect(x,y,w,h,r);if(fill){ctx.fillStyle=fill;ctx.fill()}if(stroke){ctx.strokeStyle=stroke;ctx.lineWidth=1.4;ctx.stroke()}}
function text(s,x,y,size=28,color=C.ink,weight=400,font='Arial',align='left'){ctx.fillStyle=color;ctx.font=`${weight} ${size}px ${font}`;ctx.textAlign=align;ctx.fillText(s,x,y);ctx.textAlign='left'}
function line(x,y,x2,y2,color=C.line,width=1){ctx.strokeStyle=color;ctx.lineWidth=width;ctx.beginPath();ctx.moveTo(x,y);ctx.lineTo(x2,y2);ctx.stroke()}
function pill(label,x,y,w,color=C.muted,bg=C.panel){box(x,y,w,38,19,bg);text(label,x+w/2,y+26,19,color,600,'Arial','center')}
function money(n){return '$'+(n/100).toFixed(2)}
function check(x,y,color=C.green){ctx.strokeStyle=color;ctx.lineWidth=3;ctx.beginPath();ctx.moveTo(x,y);ctx.lineTo(x+6,y+6);ctx.lineTo(x+17,y-7);ctx.stroke()}
function clockFor(t,m){if(m==='blog')return t;if(t<3)return 24+t*1.5;if(t<22)return (t-3)*27/19;return 28+(t-22)*14/8}
function phase(b){if(b<4)return 0;if(b<9)return 1;if(b<17)return 2;if(b<24)return 3;if(b<28)return 4;if(b<36)return 5;return 6}
function card(name,x,b){
 const soft=name==='soft',color=soft?C.green:C.blue, letter=soft?'A':'B';
 let changed=ease((b-(soft?10:11.5))/1.2);
 if(soft&&b>=25)changed=1-ease((b-25)/1.4);
 const modified=changed>.5, restored=soft&&b>=26.4, flashing=soft&&b>=24&&b<28;
 const base=DATA.states.baseline[name], state=modified?DATA.states.experiment[name]:base;
 box(x,246,844,636,20,C.card,flashing?C.yellow:C.line);
 if(flashing){ctx.save();ctx.globalAlpha=.12;box(x,246,844,636,20,C.yellow);ctx.restore()}
 box(x+28,275,49,49,12,color);text(letter,x+52.5,310,31,C.bg,700,'Arial','center');
 text('Agent '+letter,x+96,305,35,C.ink,600);text(soft?'worktree / soft':'worktree / move',x+96,335,21,C.muted,400,'monospace');
 pill(soft?'ARCHIVE FLAG':'ARCHIVE TABLE',x+608,283,206,color);
 line(x+28,357,x+816,357);
 text('INVOICES',x+30,397,20,C.muted,700);text('AMOUNT',x+568,397,20,C.muted,700);text('STATUS',x+720,397,20,C.muted,700);
 const rows=base.app.invoices;
 rows.forEach((r,i)=>{const y=441+i*56;ctx.save();if(i===2){ctx.globalAlpha=1-changed;if(restored&&b<28){box(x+20,y-35,804,48,8,'#31452b')}}
 text(String(r.id).padStart(2,'0'),x+31,y,26,C.muted,500,'monospace');text(r.customer,x+94,y,30,C.ink,500);text(money(r.amount_cents),x+568,y,27,C.ink,400,'monospace');text(r.paid?'Paid':'Open',x+720,y,26,r.paid?C.green:C.muted,500);ctx.restore()});
 if(changed>.9){text('1 paid invoice archived',x+94,553,24,C.muted);check(x+44,545,color)}
 box(x+28,588,788,112,12,C.panel);
 text('DATABASE',x+48,617,18,C.muted,700);
 const count=state.data.invoices.length;
 text('invoices',x+48,652,27,C.ink,500,'monospace');text(count+' rows',x+330,652,27,color,600,'monospace');
 if(modified&&soft)text('invoice 3: archived = true',x+48,685,24,color,400,'monospace');
 else if(modified)text('invoice_archive: 1 row (#3)',x+48,685,24,color,400,'monospace');
 else text('original schema · no archive',x+48,685,24,C.muted,400,'monospace');
 const dbdetail=modified?(soft?'NEW COLUMN':'NEW TABLE'):(restored?'RESTORED':'BASELINE');pill(dbdetail,x+590,611,206,restored?C.green:color,C.card);
 box(x+28,720,788,94,12,C.panel);
 text('CODE',x+48,749,18,C.muted,700);text('app.py',x+130,749,21,C.ink,500,'monospace');text('v'+state.app.version,x+746,749,23,color,700,'monospace');
 text(modified?(soft?'WHERE NOT archived':'COUNT(*) FROM invoice_archive'):'SELECT ... FROM invoices',x+48,789,26,modified?color:C.muted,400,'monospace');
 const status=restored?'Code + schema + rows restored':(!soft&&b>=26.4?'Experiment preserved':(modified?'Agent changes verified':(b>=4?'Starting checkpoint saved':'Same three-invoice seed')));
 if(b>=4)check(x+35,852,color);else{ctx.fillStyle=color;ctx.beginPath();ctx.arc(x+44,850,5,0,Math.PI*2);ctx.fill()}
 text(status,x+69,859,24,restored||(!soft&&b>=26.4)?color:C.muted,500);
}
function draw(t=0,m='blog'){
 if(!DATA)return;const b=clockFor(t,m),p=phase(b);ctx.fillStyle=C.bg;ctx.fillRect(0,0,W,H);
 // Subtle ruled texture, kept below the type.
 ctx.save();ctx.globalAlpha=.13;for(let x=0;x<W;x+=64)line(x,0,x,H,'#41514e');for(let y=0;y<H;y+=64)line(0,y,W,y,'#41514e');ctx.restore();
 box(80,46,18,29,3,C.green);box(105,34,18,41,3,C.green);text('forklift',140,73,38,C.ink,700);
 text('REAL AGENT RUN  /  CONDENSED REPLAY',1840,65,21,C.muted,500,'monospace','right');
 const titles=['Two agents. One starting point.','Save the starting point.','Two approaches, in parallel.','Same app. Different databases.','Rewind only Agent A.','Code. Schema. Rows. Back together.','Try both. Keep what works.'];
 const subs=['Separate worktrees. Separate Postgres databases.','A checkpoint pairs selected code with database state.','A adds a flag. B moves the row into an archive table.','Both apps hide invoice 3. Their stored data is different.','Both agents have finished. Restore A’s saved pair.','A is back at baseline. B keeps its entire experiment.','Forklift rewinds selected code and local Postgres together.'];
 let title=titles[p],sub=subs[p];if(m==='social'&&t<3){title='Undo one agent. Keep the other.';sub='Code and database, restored together.'}
 text(title,80,157,66,C.ink,650);text(sub,82,206,30,C.muted);
 card('soft',80,b);card('move',996,b);
 box(80,912,1760,80,14,p===4?'#302d23':C.panel,C.line);
 let label,command,color=C.ink;
 if(p<2){label='CHECKPOINT';command='forklift project capture baseline';color=C.green}
 else if(p<4){label='REAL AGENTS';command='Code edited · migrations committed · both apps verified'}
 else if(p===4){label='WORKTREE A';command='forklift project restore baseline';color=C.yellow}
 else if(p===5){label='VERIFIED';command='A = original code + schema + rows     B = unchanged';color=C.green}
 else{label='EXPLORE';command='github.com/alanagoyal/forklift';color=C.green}
 pill(label,102,933,185,color,C.card);text(command,318,964,31,color,500,'monospace');
 const stages=['START','SAVE','EXPERIMENT','COMPARE','REWIND A','VERIFY'];
 const idx=Math.min(p,5);for(let i=0;i<6;i++){const x=80+i*298;line(x,1026,x+270,1026,i<=idx?C.green:C.line,3);text(stages[i],x,1058,18,i===idx?C.ink:C.muted,600,'monospace')}
 ctx.fillStyle=C.green;ctx.beginPath();ctx.arc(80+1760*(t/(m==='blog'?42:30)),1008,3.5,0,Math.PI*2);ctx.fill();
 document.querySelector('#scrub').value=t;
}
function loop(now){if(!playing)return;time=Math.min((now-startAt)/1000,mode==='blog'?42:30);draw(time,mode);if(time<(mode==='blog'?42:30))requestAnimationFrame(loop);else{playing=false;document.querySelector('#play').textContent='Replay'}}
function play(){if(recording)return;playing=!playing;if(playing){if(time>=(mode==='blog'?42:30))time=0;startAt=performance.now()-time*1000;requestAnimationFrame(loop)}document.querySelector('#play').textContent=playing?'Pause':'Play'}
function select(m){if(recording)return;playing=false;mode=m;time=0;document.querySelector('#scrub').max=m==='blog'?42:30;document.querySelectorAll('nav button').forEach(x=>x.classList.remove('active'));document.querySelector('#'+m).classList.add('active');document.querySelector('#play').textContent='Play';draw(0,m)}
document.querySelector('#blog').onclick=()=>select('blog');document.querySelector('#social').onclick=()=>select('social');document.querySelector('#play').onclick=play;
document.querySelector('#scrub').oninput=e=>{playing=false;time=+e.target.value;draw(time,mode);document.querySelector('#play').textContent='Play'};
window.renderFrame=(t,m='blog')=>{playing=false;draw(t,m)};
window.recordVideo=async(m='blog')=>{
 await window.ready;select(m);recording=true;const chunks=[],duration=m==='blog'?42:30;
 const mime=['video/webm;codecs=vp9','video/webm;codecs=vp8','video/webm'].find(x=>MediaRecorder.isTypeSupported(x));
 const stream=canvas.captureStream(30),rec=new MediaRecorder(stream,{mimeType:mime,videoBitsPerSecond:12000000});
 rec.ondataavailable=e=>{if(e.data.size)chunks.push(e.data)};
 const done=new Promise(resolve=>rec.onstop=resolve);draw(0,m);rec.start(1000);let start=performance.now();
 document.querySelector('#status').textContent='Recording '+m+'…';
 await new Promise(resolve=>{function tick(now){const t=Math.min((now-start)/1000,duration);draw(t,m);if(t<duration)requestAnimationFrame(tick);else resolve()}requestAnimationFrame(tick)});
 rec.stop();await done;stream.getTracks().forEach(x=>x.stop());const blob=new Blob(chunks,{type:mime});
 const response=await fetch('/record/'+m+'.webm',{method:'POST',body:blob});if(!response.ok)throw new Error(await response.text());
 recording=false;document.querySelector('#status').textContent='Recorded '+m;return {mode:m,mime,bytes:blob.size,duration,response:await response.json()};
};
window.savePoster=async()=>{await window.ready;draw(32,'blog');const blob=await new Promise(r=>canvas.toBlob(r,'image/jpeg',.95));return (await fetch('/record/poster.jpg',{method:'POST',body:blob})).json()};
window.ready=fetch('data.json').then(r=>{if(!r.ok)throw new Error('Verified data unavailable');return r.json()}).then(d=>{DATA=d;draw(0,'blog');return true});
