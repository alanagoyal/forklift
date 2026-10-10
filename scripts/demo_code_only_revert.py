#!/usr/bin/env python3
"""One agent changes code and schema; git reverts only the code; Forklift restores both.

The agent step runs a real Codex agent through `forklift project run`. Every
command shown in the demo video is recorded in transcript.json with its exact
stdout, stderr and exit status.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

REPO = Path(__file__).resolve().parents[1]
PROMPT = """Support partial payments in this small invoice app.
Replace the boolean invoices.paid column with invoices.amount_paid_cents (integer cents, NOT NULL).
Write migrations/001_partial_payments.sql that adds amount_paid_cents, backfills it from paid
(paid invoices are fully paid), and then drops the old paid column. Apply it to $DATABASE_URL
with $PG_BIN/psql -v ON_ERROR_STOP=1 --single-transaction. Update app.py to report
amount_paid_cents and balance_cents per invoice and compute outstanding_cents from balances.
Bump VERSION to 2. Verify with python3 app.py: outstanding_cents must stay 17500.
Do not commit, do not use forklift, do not start background processes. Explain briefly."""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--binary', default=str(REPO/'target/release/forklift'))
    parser.add_argument('--pg-bin', type=Path,
                        default=Path(os.environ.get('PG_BIN', '/opt/homebrew/opt/postgresql@17/bin')))
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    app, store = root/'app', root/'store'
    records, transcript = [], []

    def run(argv, *, cwd=REPO, check=True, show=None, stdin=None, timeout=180):
        argv = list(map(str, argv))
        result = subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                                input=stdin, timeout=timeout)
        records.append({'argv': argv, 'cwd': str(cwd), 'exit': result.returncode,
                        'stdout': result.stdout, 'stderr': result.stderr})
        if show:
            transcript.append({'step': show[0], 'shown': show[1], 'argv': argv,
                               'exit': result.returncode,
                               'stdout': result.stdout, 'stderr': result.stderr})
        if check and result.returncode:
            raise RuntimeError(result.stdout + result.stderr)
        return result

    def db(*argv):
        return json.loads(run([args.binary, '--json', '--pg-bin', args.pg_bin,
                               '--home', store, *argv]).stdout)['result']

    def project(*argv, **kw):
        return run([args.binary, '--pg-bin', args.pg_bin, 'project', '--root', app, *argv],
                   cwd=app, **kw)

    def sql(statement):
        return project('run', '--', sys.executable, '-c',
                       'import os,subprocess,sys; subprocess.run([os.environ["PG_BIN"]+"/psql",'
                       'os.environ["DATABASE_URL"],"-XAt","--single-transaction",'
                       '"-v","ON_ERROR_STOP=1","-c",sys.argv[1]],check=True)', statement).stdout.strip()

    def app_run(step, **kw):
        return project('run', '--', 'python3', 'app.py',
                       show=(step, 'forklift project run -- python3 app.py'), **kw)

    def columns():
        return sql("SELECT string_agg(column_name, ',' ORDER BY ordinal_position) "
                   "FROM information_schema.columns WHERE table_name='invoices'")

    git = lambda *a, **kw: run(['git', '-C', app, *a], **kw)
    summary = {}
    try:
        shutil.copytree(REPO/'examples/quickstart', app)
        git('init', '--quiet')
        db('init', 'seed')
        db('checkpoint', 'seed', '--name', 'empty')
        project('configure', '--store', store, '--checkpoint', 'empty')
        project('setup')
        sql((app/'seed.sql').read_text())
        git('add', '.')
        git('-c', 'user.name=Forklift demo', '-c', 'user.email=demo@example.test',
            'commit', '--quiet', '-m', 'invoice app')
        before = json.loads(app_run('before').stdout)
        source = (app/'app.py').read_bytes()
        summary['columns_before'] = columns()
        project('capture', 'baseline', show=('capture', 'forklift project capture baseline'))

        project('run', '--', 'codex', '--no-daemon', 'exec', '--ignore-user-config',
                '--skip-git-repo-check', '--sandbox', 'workspace-write',
                '-c', 'sandbox_workspace_write.network_access=true', '--json', '-',
                stdin=PROMPT, timeout=1200, show=('agent', 'codex exec'))
        (root/'agent-prompt.txt').write_text(PROMPT + '\n')
        summary['agent_diff'] = git('diff').stdout
        summary['agent_files'] = git('status', '--porcelain').stdout
        migration = app/'migrations/001_partial_payments.sql'
        summary['migration'] = migration.read_text() if migration.exists() else None
        summary['columns_after_agent'] = columns()
        after_agent = json.loads(app_run('after_agent').stdout)
        if 'paid' in summary['columns_after_agent'].split(','):
            raise RuntimeError('The agent kept the paid column; the code-only revert would not break.')

        # Revert only the code, the way you would with git alone.
        git('checkout', '--', 'app.py', show=('git_revert', 'git checkout -- app.py'))
        broken = app_run('broken', check=False)
        summary['columns_after_git_revert'] = columns()
        if broken.returncode == 0:
            raise RuntimeError('Old code unexpectedly ran against the migrated schema.')

        project('stop')
        project('restore', 'baseline', show=('restore', 'forklift project restore baseline'))
        restored = json.loads(app_run('restored').stdout)
        summary['columns_after_restore'] = columns()
        ok = (restored == before and (app/'app.py').read_bytes() == source
              and summary['columns_after_restore'] == summary['columns_before'])
        summary.update(before=before, after_agent=after_agent, restored=restored,
                       broken_exit=broken.returncode, broken_stderr=broken.stderr,
                       verified_code_schema_rows_restored=ok)
        if not ok:
            raise RuntimeError('Rewind verification failed.')
    finally:
        try:
            if (store/'catalog.json').exists():
                for branch in db('list')['branches']:
                    if branch['state'] != 'failed':
                        db('stop', branch['name'])
        finally:
            (root/'commands.json').write_text(json.dumps(records, indent=2)+'\n')
            (root/'transcript.json').write_text(json.dumps(transcript, indent=2)+'\n')
            (root/'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    print(json.dumps({k: summary[k] for k in ['columns_before', 'columns_after_agent',
                      'columns_after_restore', 'broken_exit', 'verified_code_schema_rows_restored']},
                     indent=2))


if __name__ == '__main__':
    main()
