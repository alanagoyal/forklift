#!/usr/bin/env python3
"""Serve actual PTY output in two terminal panes and capture their display."""
import concurrent.futures,fcntl,json,os,pty,select,shlex,signal,struct,subprocess,sys,termios,threading,time,traceback
from http.server import ThreadingHTTPServer,SimpleHTTPRequestHandler
from pathlib import Path
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT.parent/'scripts'))
from demo_parallel import Demo,save
from paired_support import BIN
D=Demo(ROOT.parent/'benchmark-data/terminal-demo-2026-10-09',ROOT/'evidence')
streams={'soft':[],'move':[]}; cond=threading.Condition(); stage='ready'; started=False; epochs=[]
def emit(name,data):
    if isinstance(data,str):data=data.encode()
    with cond:streams[name].append(data);cond.notify_all()
    with (ROOT/'recording'/f'{name}.jsonl').open('a') as f:
        import base64
        f.write(json.dumps({'time':time.time(),'data':base64.b64encode(data).decode()})+'\n')
def note(name,s):emit(name,'\r\n\x1b[1;36m# '+s+'\x1b[0m\r\n')
def command(name,argv,stdin=None):
    emit(name,'\r\n\x1b[1;32m'+name+' $ \x1b[0m'+shlex.join(argv)+(' < task.txt' if stdin else '')+'\r\n')
    master,slave=pty.openpty();fcntl.ioctl(slave,termios.TIOCSWINSZ,struct.pack('HHHH',48,84,0,0))
    env=os.environ.copy();env.update(PATH=str(BIN.parent)+os.pathsep+env['PATH'],TERM='xterm-256color',COLUMNS='84',LINES='48',PYTHONDONTWRITEBYTECODE='1')
    inp=open(stdin) if stdin else subprocess.DEVNULL
    p=subprocess.Popen(argv,cwd=D.root/name,env=env,stdin=inp,stdout=slave,stderr=slave,start_new_session=True)
    os.close(slave);begin=time.time()
    try:
        while True:
            ready,_,_=select.select([master],[],[],.2)
            if ready:
                try:data=os.read(master,65536)
                except OSError:break
                if not data:break
                emit(name,data)
            elif p.poll() is not None:break
            if time.time()-begin>600:os.killpg(p.pid,signal.SIGKILL);raise TimeoutError('owned command timed out')
        code=p.wait()
        epochs.append({'pane':name,'argv':argv,'start':begin,'end':time.time(),'exit':code})
        if code:raise RuntimeError(f'{name} command exited {code}')
    finally:
        os.close(master)
        if stdin:inp.close()

def both(fn):
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        jobs=[pool.submit(fn,n) for n in ('soft','move')]
        for j in jobs:j.result()

def run():
    global stage
    try:
        stage='baseline'
        for n in streams:
            note(n,'Starting checkpoint already captured during setup')
            command(n,['forklift','project','run','--','python3','src/app.py'])
            baseline=D.observe(n);assert baseline==json.loads((D.evidence/f'{n}-baseline.json').read_text())
        time.sleep(4)
        stage='parallel agents'
        # Each launch performs short store admission; delay B until A has started.
        def agent(n):
            if n=='move':time.sleep(2)
            note(n,'Task: '+('add archived flag; archive paid invoice 3' if n=='soft' else 'move paid invoice 3 to invoice_archive'))
            command(n,['forklift','project','run','--','codex','--no-daemon','exec','--ignore-user-config','--skip-git-repo-check','--sandbox','workspace-write','-c','sandbox_workspace_write.network_access=true','--color','always','-'],D.evidence/f'{n}-prompt.txt')
        both(agent)
        intervals=[x for x in epochs if 'codex' in x['argv']]
        assert min(x['end'] for x in intervals)>max(x['start'] for x in intervals)
        modified={n:D.observe(n) for n in streams}
        assert modified['soft']['data']['invoices'][2]['archived']
        assert modified['move']['data']['invoice_archive'][0]['id']==3
        for n in streams:
            save(D.evidence/f'{n}-terminal-modified.json',modified[n])
            assert [i['id'] for i in modified[n]['app']['invoices']]==[1,2]
            D.project(n,'capture','experiment')
        stage='compare'
        for n in streams:
            note(n,'Agent finished. Check the real application and database:')
            command(n,['forklift','project','run','--','python3','inspect_db.py'])
        time.sleep(7)
        stage='rewind A'
        note('move','No restore here. Keep this experiment unchanged.')
        note('soft','Restore ONLY this worktree to its saved baseline')
        command('soft',['bash','-o','pipefail','-c','forklift --json project restore baseline | python3 show_pair.py'])
        stage='verify'
        final={n:D.observe(n) for n in streams}
        assert final['soft']==json.loads((D.evidence/'soft-baseline.json').read_text())
        assert final['move']==modified['move']
        assert D.control()==json.loads((D.evidence/'control-before.json').read_text())
        for n in streams:
            save(D.evidence/f'{n}-terminal-final.json',final[n])
            command(n,['forklift','project','run','--','python3','inspect_db.py'])
            note(n,'PASS: '+('exact baseline code, schema, rows and app output restored' if n=='soft' else 'code, schema, rows and app output unchanged'))
        note('soft','PASS: original seed unchanged; saved experiment retained')
        time.sleep(10)
        D.cleanup()
        save(D.evidence/'terminal-commands.json',epochs)
        stage='complete'
    except BaseException:
        stage='failed';emit('soft',traceback.format_exc());D.cleanup();save(D.evidence/'terminal-commands.json',epochs)

inspect='''import os,subprocess,json
from pathlib import Path
print("APPLICATION")
subprocess.run(["python3","src/app.py"],check=True)
print("\\nDATABASE ROWS")
base=[os.environ["PG_BIN"]+"/psql",os.environ["DATABASE_URL"],"-X","-v","ON_ERROR_STOP=1"]
subprocess.run(base+["-c","TABLE invoices;"],check=True)
tables=subprocess.check_output(base+["-Atc","SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"],text=True)
if "invoice_archive" in tables:subprocess.run(base+["-c","TABLE invoice_archive;"],check=True)
print("\\nSCHEMA COLUMNS")
subprocess.run(base+["-c","SELECT table_name, column_name FROM information_schema.columns WHERE table_schema='public' ORDER BY table_name, ordinal_position;"],check=True)
'''
for n in streams:
    (D.root/n/'inspect_db.py').write_text(inspect)
    (D.root/n/'show_pair.py').write_text('import json,sys\nx=json.load(sys.stdin)\nassert x["ok"],x\np=x["result"]["pair"]["current"]\nprint("Selected source:",p["code"])\nprint("Selected database:",p["branch"])\nprint("Restore complete.")\n')
class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*args,**kw):super().__init__(*args,directory=str(ROOT),**kw)
    def do_GET(self):
        if self.path.startswith('/events/'):
            name=self.path.rsplit('/',1)[1]
            if name not in streams:self.send_error(404);return
            import base64
            self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Cache-Control','no-cache');self.end_headers();index=0
            try:
                while True:
                    with cond:
                        if len(streams[name])<=index:cond.wait(timeout=2)
                        pending=streams[name][index:];index=len(streams[name])
                    if not pending:self.wfile.write(b': keepalive\n\n')
                    for chunk in pending:self.wfile.write(('data: '+json.dumps({'data':base64.b64encode(chunk).decode()})+'\n\n').encode())
                    self.wfile.flush()
            except (BrokenPipeError,ConnectionResetError):pass
        elif self.path=='/status':self.reply({'stage':stage})
        else:super().do_GET()
    def reply(self,value):
        body=json.dumps(value).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    def do_POST(self):
        global started
        if self.headers.get('Origin') not in ('http://127.0.0.1:8789','http://localhost:8789'):self.send_error(403);return
        if self.path=='/start':
            if started:self.send_error(409,'already started');return
            started=True;threading.Thread(target=run,daemon=True).start();self.reply({'started':True})
        elif self.path=='/record':
            length=int(self.headers.get('Content-Length',0))
            if not 0<length<300_000_000:self.send_error(400);return
            p=ROOT/'exports/live-terminal.webm';p.write_bytes(self.rfile.read(length));self.reply({'bytes':p.stat().st_size})
        else:self.send_error(404)
ThreadingHTTPServer(('0.0.0.0',8789),Handler).serve_forever()
