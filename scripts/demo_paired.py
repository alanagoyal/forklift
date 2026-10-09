#!/usr/bin/env python3
"""Prepare a real agent workspace, then verify and rewind it. No scripted mutation."""
import argparse
import difflib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
from paired_support import BIN, PG, Workspace, initialize, inventory, pair_command, pair_json

REPO = Path(__file__).resolve().parents[1]


def command(argv, **kwargs):
    p = subprocess.run([str(x) for x in argv], capture_output=True, text=True, timeout=180, **kwargs)
    if p.returncode:
        raise RuntimeError(p.stdout + p.stderr)
    return p.stdout.strip()


def sql(w, branch, query):
    w.cli('start', branch)
    url = w.cli('connection', branch)['connection']
    return command([PG/'psql', url, '-XAt', '-v', 'ON_ERROR_STOP=1', '-c', query])


def observe(w):
    branch = w.current()['branch']
    app = json.loads(command(pair_command(w.root, 'run', '--', sys.executable, 'app.py')))
    result = {'app': app, 'source': inventory(w.root/w.current()['code']),
            'schema': sql(w, branch, "SELECT column_name || ':' || data_type FROM information_schema.columns WHERE table_name='invoices' ORDER BY ordinal_position"),
            'data': json.loads(sql(w, branch, 'SELECT json_agg(i ORDER BY id) FROM invoices i')),
            'durability': sql(w, branch, "SELECT current_setting('fsync'),current_setting('full_page_writes'),current_setting('synchronous_commit'),current_setting('data_checksums')")}
    assert result['durability'] == 'on|on|on|on'
    return result


def save(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True)+'\n')


def prepare(root, evidence):
    root.mkdir(parents=True, exist_ok=False)
    evidence.mkdir(parents=True, exist_ok=False)
    store = root/'store'
    for args in [('init','source'), ('start','source')]:
        command([BIN,'--home',store,*args])
    url = json.loads(command([BIN,'--home',store,'--json','connection','source']))['result']['connection']
    command([PG/'psql',url,'-X','-v','ON_ERROR_STOP=1','-f',REPO/'fixtures/invoice-app/seed.sql'])
    command([BIN,'--home',store,'checkpoint','source','--name','seed'])
    command([BIN,'--home',store,'stop','source'])
    w = initialize(root/'workspace',REPO/'fixtures/invoice-app',store,'seed')
    w.cli('branch','--from','seed','--name','sibling')
    save(evidence/'baseline.json',observe(w))
    start=time.monotonic()
    pair=pair_json(w.root,'capture','baseline')
    save(evidence/'capture.json',{'elapsed_seconds':time.monotonic()-start,'pair':pair})
    (evidence/'prompt.txt').write_text('''Implement invoice archiving in this owned isolated fixture workspace. You must actually edit app.py, write and execute a PostgreSQL migration against DATABASE_URL, and change invoice data. Add archived boolean NOT NULL DEFAULT false, archive paid invoice id 3, and update the app to list only unarchived invoices. Its JSON should have version 2, two visible invoices, archived_count 1, and outstanding_cents 17500. Update its executable assertions and run python3 app.py to verify. Use Python subprocess with PG_BIN/psql and DATABASE_URL from the environment. Keep all PostgreSQL durability settings enabled. Do not edit outside this working directory, change other branches, commit, or launch background processes. Record your migration in migration.sql and explain exactly what you changed. This is a real coding task; do not simulate an agent receipt.\n''')
    print(w.root)


def agent(root, evidence):
    """One bounded provider invocation; transcript remains separate from test mutations."""
    cmd = pair_command(root/'workspace', 'run', '--', 'codex', '--no-daemon', 'exec', '--ignore-user-config',
           '--skip-git-repo-check', '--sandbox', 'workspace-write',
           '-c', 'sandbox_workspace_write.network_access=true', '--json', '-')
    save(evidence/'agent-command.json', cmd)
    # Refuse to replace an earlier transcript; use a fresh demo for a new run.
    with (evidence/'prompt.txt').open() as prompt, (evidence/'agent.jsonl').open('x') as out, (evidence/'agent.stderr').open('x') as err:
        process = subprocess.Popen(cmd, stdin=prompt, stdout=out, stderr=err, start_new_session=True)
        try:
            status = process.wait(timeout=600)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            (evidence/'agent-exit.txt').write_text('timeout after 600 seconds; process group killed\n')
            raise
    (evidence/'agent-exit.txt').write_text(str(status)+'\n')
    if status:
        raise RuntimeError('agent failed; inspect transcript, then stop owned servers')


def verify_agent_receipts(evidence):
    events = [json.loads(line) for path in sorted(evidence.glob('agent*.jsonl'))
              for line in path.read_text().splitlines() if line.strip()]
    completed = [e['item'] for e in events if e.get('type') == 'item.completed']
    assert any(i.get('type') == 'file_change' and i.get('status') == 'completed'
               and any(c['path'].endswith('/app.py') for c in i.get('changes', [])) for i in completed), 'missing actual agent code edit receipt'
    assert any(i.get('type') == 'command_execution' and i.get('exit_code') == 0
               and 'ALTER TABLE' in i.get('aggregated_output', '')
               and 'UPDATE 1' in i.get('aggregated_output', '') for i in completed), 'missing actual agent migration/data execution receipt'


def finish(root,evidence):
    w=Workspace(root/'workspace')
    try:
        verify_agent_receipts(evidence)
        baseline=json.loads((evidence/'baseline.json').read_text())
        modified=observe(w)
        assert modified['app']['version']==2 and modified['app']['archived_count']==1
        assert len(modified['app']['invoices'])==2
        assert modified['data']!=baseline['data'] and modified['schema']!=baseline['schema']
        assert modified['source']!=baseline['source']
        save(evidence/'modified.json',modified)
        shutil.copytree(w.root/w.current()['code'],evidence/'agent-source')
        before=REPO/'fixtures/invoice-app/app.py'
        after=evidence/'agent-source/app.py'
        (evidence/'code.diff').write_text(''.join(difflib.unified_diff(before.read_text().splitlines(True),after.read_text().splitlines(True),fromfile='baseline/app.py',tofile='agent/app.py')))
        # Demonstrate why merely running old code does not rewind the stored state.
        branch=w.current()['branch']
        env=os.environ.copy(); env.update(DATABASE_URL=w.cli('connection',branch)['connection'],PG_BIN=str(PG))
        old_app=json.loads(command([sys.executable,before],env=env))
        save(evidence/'code-only.json',{'app':old_app,'database_still_modified':sql(w,branch,"SELECT archived FROM invoices WHERE id=3")=='t'})
        pair_json(w.root,'capture','modified')
        start=time.monotonic()
        pair_json(w.root,'restore','baseline')
        elapsed=time.monotonic()-start
        restored=observe(w)
        assert restored==baseline,(restored,baseline)
        save(evidence/'restored.json',restored)
        isolation={}
        for name in ('source','sibling'):
            rows=json.loads(sql(w,name,'SELECT json_agg(i ORDER BY id) FROM invoices i'))
            assert rows==baseline['data']
            assert sql(w,name,"SELECT count(*) FROM information_schema.columns WHERE table_name='invoices' AND column_name='archived'")=='0'
            isolation[name]=rows
            w.cli('stop',name)
        save(evidence/'summary.json',{'restore_seconds':elapsed,'isolation':isolation,'authentic_agent_receipts':[p.name for p in sorted(evidence.glob('agent*.jsonl'))],'baseline':baseline['app'],'modified':modified['app'],'restored':restored['app']})
    finally:
        for b in w.cli('list')['branches']:
            if b['state']!='failed':
                w.cli('stop',b['name'])
        view=w.cli('list')
        assert view['active_clusters']==0
        assert not list(w.store.glob('generations/*/data/postmaster.pid'))
        save(evidence/'cleanup.json',view)
    (evidence/'SUCCESS').write_text('real agent code/schema/data changes and paired rewind verified\n')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('phase',choices=['prepare','agent','finish'])
    p.add_argument('root',type=Path)
    p.add_argument('evidence',type=Path)
    a=p.parse_args()
    globals()[a.phase](a.root.resolve(),a.evidence.resolve())
