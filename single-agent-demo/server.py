#!/usr/bin/env python3
"""Serve actual PTY output in two terminal panes and capture their display."""
import concurrent.futures,fcntl,json,os,pty,select,shlex,signal,struct,subprocess,sys,termios,threading,time,traceback
from http.server import ThreadingHTTPServer,SimpleHTTPRequestHandler
from pathlib import Path
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT.parent/'scripts'))
from demo_parallel import Demo,save
from paired_support import BIN
D=Demo(ROOT.parent/'benchmark-data/single-demo-2026-10-09-v2',ROOT/'evidence-v2')
streams={'soft':[]}; cond=threading.Condition(); stage='ready'; started=False; epochs=[]
def emit(name,data):
    if isinstance(data,str):data=data.encode()
    with cond:streams[name].append(data);cond.notify_all()
    with (ROOT/'recording'/f'{name}.jsonl').open('a') as f:
        import base64
        f.write(json.dumps({'time':time.time(),'data':base64.b64encode(data).decode()})+'\n')
def note(name,s):emit(name,'\r\n\x1b[1;36m# '+s+'\x1b[0m\r\n')
def command(name,argv,stdin=None,expected=0):
    emit(name,'\r\n\x1b[1;32m'+name+' $ \x1b[0m'+(argv[2] if argv[:2]==['bash','-c'] else shlex.join(argv))+(' < task.txt' if stdin else '')+'\r\n')
    master,slave=pty.openpty();fcntl.ioctl(slave,termios.TIOCSWINSZ,struct.pack('HHHH',32,110,0,0))
    env=os.environ.copy();env.update(PATH=str(BIN.parent)+os.pathsep+env['PATH'],TERM='xterm-256color',COLUMNS='110',LINES='32',PYTHONDONTWRITEBYTECODE='1')
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
        if code != expected:raise RuntimeError(f'{name} command exited {code}')
    finally:
        os.close(master)
        if stdin:inp.close()

def scene(text):
    emit('soft','\x1b[2J\x1b[H')
    note('soft',text)
    epochs.append({'scene':text,'time':time.time()})

def run():
    global stage
    try:
        stage='baseline'
        scene('three invoices. code and database agree.')
        command('soft',['forklift','project','run','--','python3','show.py'])
        note('soft','save this starting point before the agent works.')
        command('soft',['bash','-c','forklift project capture before_demo > capture.json'])
        note('soft','starting point saved.')
        baseline=D.observe('soft');save(D.evidence/'baseline.json',baseline)
        time.sleep(5)
        stage='agent'
        scene('let the agent rename customer to client_name and delete the paid invoice.')
        command('soft',['forklift','project','run','--','codex','--no-daemon','exec','--ignore-user-config','--skip-git-repo-check','--sandbox','workspace-write','-c','sandbox_workspace_write.network_access=true','--color','always','-'],D.evidence/'single-prompt.txt')
        (D.root/'soft/agent_app.py').write_bytes((D.root/'soft/src/app.py').read_bytes())
        modified=D.observe('soft');save(D.evidence/'modified.json',modified)
        assert len(modified['data']['invoices'])==2
        assert 'client_name' in modified['data']['invoices'][0]
        assert 'customer' not in modified['data']['invoices'][0]
        stage='changed'
        scene('the new code works with the new schema. invoice 3 is gone.')
        command('soft',['cat','migrations/rename.sql'])
        command('soft',['forklift','project','run','--','python3','show.py'])
        time.sleep(6)
        stage='code-only'
        scene('try undoing just the code.')
        command('soft',['git','restore','src/app.py'])
        command('soft',['forklift','project','run','--','python3','show.py'],expected=1)
        note('soft','the old code asks for customer. postgres now has client_name.')
        command('soft',['forklift','project','run','--','python3','rows.py'])
        note('soft','git restored the code. it did not undo the migration or bring invoice 3 back.')
        time.sleep(9)
        note('soft','put the agent code back. then undo the whole change with forklift.')
        command('soft',['cp','agent_app.py','src/app.py'])
        time.sleep(3)
        stage='restore'
        scene('rewind with forklift: code, schema, and rows together.')
        note('soft','right now: changed code, renamed column, missing invoice.')
        time.sleep(3)
        command('soft',['bash','-c','forklift project restore before_demo > restore.json'])
        note('soft','saved code and database restored.')
        command('soft',['forklift','project','run','--','python3','show.py'])
        command('soft',['git','diff','--stat','--','src/app.py'])
        command('soft',['bash','-c','test ! -e migrations/rename.sql && echo "agent migration file removed"'])
        final=D.observe('soft');assert final==baseline
        assert D.control()==json.loads((D.evidence/'control-before.json').read_text())
        save(D.evidence/'restored.json',final)
        note('soft','original code. original column. all three invoices. app works again.')
        time.sleep(8)
        D.cleanup();save(D.evidence/'terminal-commands.json',epochs);stage='complete'
    except BaseException:
        stage='failed';emit('soft',traceback.format_exc());D.cleanup();save(D.evidence/'terminal-commands.json',epochs)

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
        if self.headers.get('Origin') not in ('http://127.0.0.1:8790','http://localhost:8790'):self.send_error(403);return
        if self.path=='/start':
            if started:self.send_error(409,'already started');return
            started=True;threading.Thread(target=run,daemon=True).start();self.reply({'started':True})
        elif self.path=='/record':
            length=int(self.headers.get('Content-Length',0))
            if not 0<length<300_000_000:self.send_error(400);return
            p=ROOT/'exports/live-terminal.webm';p.write_bytes(self.rfile.read(length));self.reply({'bytes':p.stat().st_size})
        else:self.send_error(404)
ThreadingHTTPServer(('0.0.0.0',8790),Handler).serve_forever()
