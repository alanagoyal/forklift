#!/usr/bin/env python3
"""Real concurrent coding agents in isolated Git worktrees and local PostgreSQL.

The harness supplies the baseline, prompts and assertions. Only the agents write
feature code or execute feature migrations. Failed attempts remain in evidence.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import threading
import time

from paired_support import BIN, PG, inventory

REPO = Path(__file__).resolve().parents[1]
NAMES = ('soft', 'move')
OBSERVE = r'''
import json, os, subprocess
url=os.environ['DATABASE_URL']; pg=os.environ['PG_BIN']
def sql(q):
    return subprocess.check_output([pg+'/psql',url,'-XAt','-v','ON_ERROR_STOP=1','-c',q],text=True).strip()
app=json.loads(subprocess.check_output(['python3','src/app.py'],text=True))
schema=subprocess.check_output([pg+'/pg_dump','--dbname',url,'--schema-only','--no-owner','--no-privileges'],text=True)
schema='\n'.join(line for line in schema.splitlines() if not line.startswith(('\\restrict ', '\\unrestrict ')))+'\n'
tables=sql("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename").splitlines()
data={}
for name in tables:
    quoted='"'+name.replace('"','""')+'"'
    data[name]=json.loads(sql('SELECT coalesce(json_agg(t ORDER BY id),\'[]\'::json) FROM '+quoted+' t'))
print(json.dumps({'app':app,'schema':schema,'data':data,'durability':sql("SELECT current_setting('fsync'),current_setting('full_page_writes'),current_setting('synchronous_commit'),current_setting('data_checksums')")}))
'''


def save(path, obj):
    path.write_text(json.dumps(obj, indent=2, sort_keys=True)+'\n')


class Demo:
    def __init__(self, root, evidence):
        self.root, self.evidence = root, evidence
        self.store, self.repo = root/'store', root/'repo'

    def command(self, argv, *, cwd=None, env=None, expected=0):
        start = time.time()
        p = subprocess.run([str(a) for a in argv], cwd=cwd, env=env,
                           text=True, capture_output=True, timeout=180)
        with (self.evidence/'commands.jsonl').open('a') as f:
            f.write(json.dumps({'argv':list(map(str,argv)), 'cwd':str(cwd),
                'started_unix':start,'finished_unix':time.time(),
                'exit':p.returncode,'stdout':p.stdout,'stderr':p.stderr})+'\n')
        if p.returncode != expected:
            raise RuntimeError(f'{argv}: {p.returncode}\n{p.stdout}\n{p.stderr}')
        return p.stdout.strip()

    def db(self, *args):
        return json.loads(self.command([BIN,'--json','--home',self.store,'--pg-bin',PG,*args]))['result']

    def argv(self, name, *args):
        return [str(BIN),'--pg-bin',str(PG),'project','--root',str(self.root/name),*map(str,args)]

    def project(self, name, *args, expected=0):
        return self.command(self.argv(name,*args),expected=expected)

    def inspect(self, name):
        return json.loads(self.command([BIN,'--json','project','--root',self.root/name,'inspect']))['result']

    def git(self, root, *args):
        return self.command(['git','-C',root,*args])

    def source(self, root):
        # Independent reader: the adapter's retained mirror is not the assertion.
        return {key:inventory(root/key) if (root/key).is_dir() else None for key in ('src','migrations')} | {
            'notes.txt': {'sha256':hashlib.sha256((root/'notes.txt').read_bytes()).hexdigest(),
                          'mode':(root/'notes.txt').stat().st_mode & 0o777}}

    def git_metadata(self, root):
        admin=Path(self.git(root,'rev-parse','--absolute-git-dir'))
        return {str(p.relative_to(root)) if p.is_relative_to(root) else p.name:
                hashlib.sha256(p.read_bytes()).hexdigest()
                for p in [root/'.git',admin/'HEAD',admin/'index',admin/'commondir']}

    def observe(self, name):
        value=json.loads(self.project(name,'run','--',sys.executable,'-c',OBSERVE))
        value['source']=self.source(self.root/name)
        assert value['durability']=='on|on|on|on'
        return value

    def control(self):
        self.db('start','seed')
        try:
            env=os.environ.copy(); env.update(DATABASE_URL=self.db('connection','seed')['uri'],PG_BIN=str(PG),PYTHONDONTWRITEBYTECODE='1')
            value=json.loads(self.command([sys.executable,'-c',OBSERVE],cwd=self.repo,env=env))
            value['source']=self.source(self.repo)
            return value
        finally:
            self.db('stop','seed')

    def hooks(self, name, kind):
        env={k:v for k,v in os.environ.items() if not k.startswith('BB_') and k != 'NODE_ENV'}
        env['PATH']=str(BIN.parent)+os.pathsep+env['PATH']
        return self.command(['env','bash',f'.bb-env-{kind}.sh'],cwd=self.root/name,env=env)

    def prepare(self):
        self.root.mkdir(parents=True,exist_ok=False)
        self.evidence.mkdir(parents=True,exist_ok=False)
        self.repo.mkdir(); (self.repo/'src').mkdir()
        shutil.copy(REPO/'fixtures/invoice-app/app.py',self.repo/'src/app.py')
        (self.repo/'src/app.py').chmod(0o751)
        (self.repo/'notes.txt').write_text('baseline notes\n')
        (self.repo/'.gitignore').write_text('__pycache__/\n.env.local\n')
        save(self.repo/'.forklift.json',{'format':1,'source':['src','migrations','notes.txt'],
             'connection_env':'DATABASE_URL','commands':{'verify':['python3','src/app.py']}})
        for kind in ('setup','teardown'):
            shutil.copy(REPO/'examples/project-hooks'/f'.bb-env-{kind}.sh',self.repo/f'.bb-env-{kind}.sh')
        self.git(self.repo,'init'); self.git(self.repo,'add','.')
        self.git(self.repo,'-c','user.name=Forklift demo','-c','user.email=demo@example.test','commit','-m','baseline invoicing fixture')
        self.db('init','seed'); self.db('start','seed')
        self.command([PG/'psql',self.db('connection','seed')['uri'],'-X','-v','ON_ERROR_STOP=1','-f',REPO/'fixtures/invoice-app/seed.sql'])
        self.db('checkpoint','seed','--name','development'); self.db('stop','seed')
        self.project('repo','configure','--store',self.store,'--checkpoint','development')
        save(self.evidence/'control-before.json',self.control())
        for name in NAMES:
            path=self.root/name
            self.git(self.repo,'worktree','add','-b',name,path)
            self.hooks(name,'setup')
            # Capture selected unstaged bytes that differ from the unchanged index.
            (path/'notes.txt').write_text('staged notes\n'); self.git(path,'add','notes.txt')
            (path/'notes.txt').write_text('dirty baseline notes\n')
            (path/'src/local.txt').write_text('selected untracked baseline\n')
            (path/'.env.local').write_text('unselected local fixture setting\n')
            save(self.evidence/f'{name}-git-before.json',self.git_metadata(path))
            save(self.evidence/f'{name}-baseline.json',self.observe(name))
            self.project(name,'capture','baseline')
            save(self.evidence/f'{name}-binding-before.json',self.inspect(name))
        bindings=[self.inspect(name) for name in NAMES]
        assert len({b['state'] for b in bindings})==2
        assert len({b['pair']['current']['branch'] for b in bindings})==2
        save(self.evidence/'identities.json', {name: {'state': b['state'], 'branch': b['pair']['current']['branch']} for name,b in zip(NAMES,bindings)})
        common='''Implement invoice archiving in this isolated worktree. Another real coding agent is simultaneously trying a different design in another worktree/database. Work only inside this working directory, using the injected DATABASE_URL and PG_BIN. Do not read/change sibling worktrees, Forklift metadata, configuration/hooks, Git index/history, notes.txt, src/local.txt, or .env.local. Do not commit. Do not use forklift inside this already managed command; its gate is held. Do not launch background processes. Keep all PostgreSQL durability settings enabled. You must actually edit src/app.py, create migrations/archive.sql, and execute that migration against DATABASE_URL with psql ON_ERROR_STOP. Use one transaction for the migration and row changes. Update app assertions and run python3 src/app.py successfully. Print JSON version 2 with visible invoices 1 and 2, archived_count 1, and outstanding_cents 17500. Do not hardcode computed invoice data. Explain what you changed.\n'''
        designs={
          'soft':'Use soft archiving: add archived boolean NOT NULL DEFAULT false to invoices, then UPDATE paid invoice id 3 to archived=true. Keep all three rows in invoices; the app filters archived rows and counts them separately.\n',
          'move':'Use a separate invoice_archive table retaining the invoice fields and primary key; physically move paid invoice id 3 into it with INSERT and DELETE in the same transaction. Do not add an archived column to invoices. The app lists active invoices and counts invoice_archive.\n'}
        for name in NAMES:
            (self.evidence/f'{name}-prompt.txt').write_text(common+designs[name])
        save(self.evidence/'setup.json',{'root':str(self.root),'binary':str(BIN),'postgres_bin':str(PG),
            'provisioning':'git worktree add; tracked BB hooks with BB_* and NODE_ENV stripped; no BB daemon provisioning',
            'forklift_revision':self.git(REPO,'rev-parse','HEAD')})

    def agents(self):
        # Admission is briefly serialized by the store lock. Wait for the first
        # actual provider event before admitting the second, then prove overlap.
        for name in NAMES:
            for suffix in ('agent.jsonl','agent.stderr','agent-command.json'):
                if (self.evidence/f'{name}-{suffix}').exists():
                    raise RuntimeError('agent attempt already exists; use fresh root/evidence paths')
        processes={}; intervals={}; events=[]; threads=[]; lock=threading.Lock()
        def collect(name, process, ready):
            with (self.evidence/f'{name}-agent.jsonl').open('x') as out:
                for line in process.stdout:
                    out.write(line); out.flush()
                    try: event=json.loads(line)
                    except json.JSONDecodeError: event={}
                    with lock:
                        events.append({'agent':name,'observed_unix':time.time(),'event':event.get('type'),'item_type':event.get('item',{}).get('type')})
                    if event.get('type')=='thread.started': ready.set()
        try:
            for name in NAMES:
                cmd=self.argv(name,'run','--','codex','--no-daemon','exec','--ignore-user-config',
                    '--skip-git-repo-check','--sandbox','workspace-write','-c','sandbox_workspace_write.network_access=true','--json','-')
                save(self.evidence/f'{name}-agent-command.json',cmd)
                with (self.evidence/f'{name}-prompt.txt').open() as prompt:
                    err=(self.evidence/f'{name}-agent.stderr').open('x')
                    process=subprocess.Popen(cmd,stdin=prompt,stdout=subprocess.PIPE,stderr=err,text=True,start_new_session=True)
                    err.close()
                processes[name]=process; intervals[name]={'started_unix':time.time(),'pid':process.pid}
                ready=threading.Event(); t=threading.Thread(target=collect,args=(name,process,ready)); t.start(); threads.append(t)
                if not ready.wait(45):
                    raise RuntimeError(f'{name} did not emit a provider thread.started event; inspect retained transcript')
            deadline=time.monotonic()+600
            while any(p.poll() is None for p in processes.values()):
                for name,p in processes.items():
                    if p.poll() is not None and 'finished_unix' not in intervals[name]:
                        intervals[name].update(finished_unix=time.time(),exit=p.returncode)
                if time.monotonic()>deadline: raise TimeoutError('agents exceeded 600 seconds')
                time.sleep(.2)
        finally:
            for name,p in processes.items():
                if p.poll() is None:
                    os.killpg(p.pid,signal.SIGKILL); p.wait()
                intervals[name].setdefault('finished_unix',time.time()); intervals[name]['exit']=p.returncode
            for t in threads: t.join(timeout=10)
            save(self.evidence/'agent-intervals.json',intervals)
            save(self.evidence/'agent-event-times.json',events)
        assert set(intervals)==set(NAMES) and all(v['exit']==0 for v in intervals.values()),intervals
        overlap=min(v['finished_unix'] for v in intervals.values())-max(v['started_unix'] for v in intervals.values())
        assert overlap>0, 'agent processes did not overlap'
        save(self.evidence/'overlap.json',{'process_overlap_seconds':overlap,'intervals':intervals,
            'note':'process overlap is concurrency evidence, not a performance measurement'})

    def receipts(self, name):
        events=[json.loads(line) for line in (self.evidence/f'{name}-agent.jsonl').read_text().splitlines() if line.strip()]
        items=[event['item'] for event in events if event.get('type')=='item.completed']
        assert any(i.get('type')=='file_change' and i.get('status')=='completed' and
                   any(c['path'].endswith('/src/app.py') for c in i.get('changes',[])) for i in items),f'{name}: missing app edit receipt'
        output='\n'.join(i.get('aggregated_output','') for i in items if i.get('type')=='command_execution' and i.get('exit_code')==0)
        required=['BEGIN','COMMIT']+(['ALTER TABLE','UPDATE 1'] if name=='soft' else ['CREATE TABLE','DELETE 1'])
        assert all(token in output for token in required),f'{name}: missing migration execution receipt'
        assert any(i.get('type')=='command_execution' and i.get('exit_code')==0 and
                   'migrations/archive.sql' in i.get('command','') and
                   all(token in i.get('aggregated_output','') for token in required) for i in items), f'{name}: migration file was not executed successfully'
        migration=(self.root/name/'migrations/archive.sql').read_text().upper()
        assert ('UPDATE ' in migration if name=='soft' else 'INSERT INTO ' in migration and 'DELETE FROM ' in migration)

    def finish(self):
        bindings=[self.inspect(name) for name in NAMES]
        assert len({b['state'] for b in bindings})==2
        assert len({b['pair']['current']['branch'] for b in bindings})==2
        save(self.evidence/'identities.json', {name: {'state': b['state'], 'branch': b['pair']['current']['branch']} for name,b in zip(NAMES,bindings)})
        events=json.loads((self.evidence/'agent-event-times.json').read_text())
        turns={}
        for name in NAMES:
            selected=[e for e in events if e['agent']==name]
            turns[name]={'started_unix':next(e['observed_unix'] for e in selected if e['event']=='turn.started'),
                         'completed_unix':next(e['observed_unix'] for e in selected if e['event']=='turn.completed')}
        overlap=min(t['completed_unix'] for t in turns.values())-max(t['started_unix'] for t in turns.values())
        assert overlap>0, 'actual provider turns did not overlap'
        save(self.evidence/'provider-overlap.json', {'turns':turns,'overlap_seconds':overlap,
             'clock':'approximate wall-clock event receipt times in parent harness; not model execution timings'})
        for name in NAMES: self.receipts(name)
        modified={}
        for name in NAMES:
            baseline=json.loads((self.evidence/f'{name}-baseline.json').read_text())
            modified[name]=self.observe(name)
            app=modified[name]['app']
            assert app['version']==2 and app['archived_count']==1 and app['outstanding_cents']==17500
            assert [i['id'] for i in app['invoices']]==[1,2]
            assert all(modified[name][k]!=baseline[k] for k in ('source','schema','data'))
            save(self.evidence/f'{name}-modified.json',modified[name])
            shutil.copytree(self.root/name/'src',self.evidence/f'{name}-agent-source'/'src',dirs_exist_ok=True)
            shutil.copytree(self.root/name/'migrations',self.evidence/f'{name}-agent-source'/'migrations',dirs_exist_ok=True)
            (self.evidence/f'{name}-code.diff').write_text(self.git(self.root/name,'diff','--','src/app.py'))
            state=Path(self.inspect(name)['state'])
            existing=state/'pair/pairs/experiment.json'
            if existing.exists():
                saved=json.loads(existing.read_text())
                assert inventory(state/'pair'/saved['code'])==saved['files']
                assert self.source(state/'pair'/saved['code'])==modified[name]['source']
            else:
                self.project(name,'capture','experiment')
        assert len(modified['soft']['data']['invoices'])==3 and modified['soft']['data']['invoices'][2]['archived']
        assert len(modified['move']['data']['invoices'])==2 and modified['move']['data']['invoice_archive'][0]['id']==3
        before=self.inspect('move')
        self.project('soft','restore','baseline')
        restored=self.observe('soft'); sibling=self.observe('move')
        assert restored==json.loads((self.evidence/'soft-baseline.json').read_text())
        assert sibling==modified['move']
        assert self.inspect('move')==before
        save(self.evidence/'soft-restored.json',restored)
        save(self.evidence/'move-after-sibling-rewind.json',sibling)
        for name in NAMES:
            assert self.git_metadata(self.root/name)==json.loads((self.evidence/f'{name}-git-before.json').read_text())
            assert (self.root/name/'.env.local').read_text()=='unselected local fixture setting\n'
            save(self.evidence/f'{name}-binding-after.json',self.inspect(name))
        assert self.control()==json.loads((self.evidence/'control-before.json').read_text())
        save(self.evidence/'control-after.json',self.control())
        # The rejected design remains a named saved pair with its exact manifest.
        soft=self.inspect('soft'); state=Path(soft['state'])
        saved=json.loads((state/'pair/pairs/experiment.json').read_text())
        assert inventory(state/'pair'/saved['code'])==saved['files']
        save(self.evidence/'retained-experiment.json',saved)
        for name in NAMES: self.hooks(name,'teardown')
        self.cleanup()
        save(self.evidence/'summary.json',{'agents':2,'real_git_worktrees':2,'same_seed':True,
            'independent_database_branches':True,'restored':'soft','sibling_unchanged':'move',
            'seed_control_unchanged':True,'exact_code_schema_rows_app_restored':True,
            'git_index_head_and_unselected_files_preserved':True,'rejected_experiment_retained':True,
            'bb_hooks_executed':True,'bb_native_provisioning_exercised':False,
            'baseline':restored['app'],'soft_experiment':modified['soft']['app'],'move_experiment':modified['move']['app']})
        (self.evidence/'SUCCESS').write_text('real concurrent agents; isolated code/schema/rows; one paired rewind; sibling/control unchanged; all servers stopped\n')

    def cleanup(self):
        if not self.store.exists(): return
        for branch in self.db('list')['branches']:
            if branch['state']!='failed': self.db('stop',branch['name'])
        view=self.db('list')
        assert view['active_clusters']==0
        assert not list(self.store.glob('generations/*/data/postmaster.pid'))
        save(self.evidence/'cleanup.json',view)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase',choices=['prepare','agents','finish','cleanup','all'])
    parser.add_argument('root',type=Path); parser.add_argument('evidence',type=Path)
    args=parser.parse_args(); demo=Demo(args.root.resolve(),args.evidence.resolve())
    try:
        if args.phase=='all':
            demo.prepare(); demo.agents(); demo.finish()
        else: getattr(demo,args.phase)()
    except BaseException:
        if demo.evidence.exists(): demo.cleanup()
        raise
