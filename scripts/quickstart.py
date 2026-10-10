#!/usr/bin/env python3
"""Create a disposable Git project, change code and SQL, then rewind both."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

REPO = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path, help='new directory for the app, store and command log')
    parser.add_argument('--binary', default=os.environ.get('FORKLIFT_BIN', 'forklift'))
    parser.add_argument('--pg-bin', type=Path,
                        default=Path(os.environ.get('PG_BIN', '/opt/homebrew/opt/postgresql@17/bin')))
    args = parser.parse_args()
    binary = shutil.which(str(args.binary))
    if not binary:
        parser.error('forklift is not installed; run cargo install --path . --locked --bin forklift, '
                     'or pass --binary /path/to/forklift')
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    app, store = root/'app', root/'store'
    records = []

    def run(argv, *, cwd=REPO):
        argv = list(map(str, argv))
        result = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=180)
        records.append({'argv': argv, 'exit': result.returncode,
                        'stdout': result.stdout, 'stderr': result.stderr})
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)
        return result.stdout.strip()

    def db(*argv):
        return json.loads(run([binary, '--json', '--pg-bin', args.pg_bin,
                               '--home', store, *argv]))['result']

    def project(*argv):
        return run([binary, 'project', '--root', app, *argv])

    def sql(statement):
        # The child receives the current URL on every invocation, including after rewind.
        return project('run', '--', sys.executable, '-c',
                       'import os,subprocess,sys; subprocess.run([os.environ["PG_BIN"]+"/psql",'
                       'os.environ["DATABASE_URL"],"-XAt","--single-transaction",'
                       '"-v","ON_ERROR_STOP=1","-c",sys.argv[1]],check=True)', statement)

    def report(label):
        value = json.loads(project('task', 'report'))
        print(f'{label}: version {value["version"]}, {len(value["invoices"])} invoices, '
              f'${value["outstanding_cents"] / 100:.2f} outstanding', flush=True)
        return value

    try:
        shutil.copytree(REPO/'examples/quickstart', app)
        run(['git', 'init', '--quiet', app])
        # Git commits are optional: selected dirty and untracked files are captured too.
        db('init', 'seed')
        db('checkpoint', 'seed', '--name', 'empty')
        project('configure', '--store', store, '--checkpoint', 'empty')
        project('setup')
        sql((app/'seed.sql').read_text())
        before = report('before')
        source = (app/'app.py').read_bytes()
        project('capture', 'baseline')

        # These are deterministic demo edits, not an agent-generated experiment.
        (app/'app.py').write_bytes(source.replace(b'VERSION = 1', b'VERSION = 2'))
        sql('ALTER TABLE invoices ADD COLUMN experiment_note text; DELETE FROM invoices WHERE id=2;')
        changed = report('experiment')
        if changed['version'] != 2 or len(changed['invoices']) != 2:
            raise RuntimeError('The demonstration did not apply its code and data changes.')
        project('restore', 'baseline')
        after = report('restored')
        columns = sql("SELECT count(*) FROM information_schema.columns "
                      "WHERE table_name='invoices' AND column_name='experiment_note'")
        if after != before or (app/'app.py').read_bytes() != source or columns != '0':
            raise RuntimeError('Rewind verification failed: code, schema or rows differ from baseline.')
    finally:
        try:
            # Only this new demo's store is touched, including on an ordinary error.
            if (store/'catalog.json').exists():
                for branch in db('list')['branches']:
                    if branch['state'] != 'failed':
                        db('stop', branch['name'])
                if db('list')['active_clusters'] != 0:
                    raise RuntimeError('Demo databases did not all stop.')
        finally:
            (root/'commands.json').write_text(json.dumps(records, indent=2)+'\n')
    print('All demo databases are stopped.', flush=True)
    print(f'Application: {app}\nDatabase store: {store}\nCommand log: {root/"commands.json"}')


if __name__ == '__main__':
    main()
