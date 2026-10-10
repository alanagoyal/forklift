#!/usr/bin/env python3
"""Reproducible local Forklift demo; Python standard library only.
Run from any directory. Existing output directories are never adopted.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import statistics
import subprocess
import sys
import time

REPO = Path(__file__).resolve().parents[1]


def process_usage(view):
    parents = {b['process']['pid'] for b in view['branches'] if b['process']}
    table = {}
    for line in subprocess.check_output(['ps', '-axo', 'pid=,ppid=,rss='], text=True).splitlines():
        pid, ppid, rss = map(int, line.split()); table[pid] = (ppid, rss)
    included = set(parents)
    while True:
        children = {pid for pid, (parent, _) in table.items() if parent in included}
        if children.issubset(included): break
        included |= children
    return {'clusters': len(parents), 'processes': len(included),
            'sum_rss_kib': sum(table[p][1] for p in included if p in table)}


def tree(view):
    lines = [f"running clusters: {view['active_clusters']}/{view['max_running']}"]
    groups = {}
    for b in view['branches']: groups.setdefault(b['checkpoint'], []).append(b)
    for parent, branches in groups.items():
        lines.append(f"checkpoint {parent}" if parent else 'independent / restored branches')
        for b in branches:
            suffix = ' (restart pending)' if b['resume_pending'] else ''
            lines.append(f"  └─ {b['name']}: {b['activity']}{suffix}")
    for c in view['checkpoints']:
        lines.append(f"saved {c['name']} ← {c['source']} [{c['state']}]")
    return '\n'.join(lines)


class Demo:
    def __init__(self, args):
        self.root = args.output.resolve()
        self.bin = args.binary.resolve()
        self.pg = args.pg_bin.resolve()
        self.rows = args.rows
        self.source = self.root/'source'
        self.target = self.root/'restored'
        self.commands, self.queries, self.snapshots = [], [], []
        self.summary = {'status': 'running', 'rows': self.rows, 'platform': platform.platform(),
                        'binary_sha256': hashlib.sha256(self.bin.read_bytes()).hexdigest(),
                        'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                        'fixture_sha256': hashlib.sha256((REPO/'fixtures/seed.sql').read_bytes()).hexdigest(),
                        'source': str(self.source), 'restored': str(self.target),
                        'timing_note': 'repeated local run; filesystem cache uncontrolled; no cold-disk claim'}

    def cli(self, *args, home=None):
        t = time.monotonic()
        env = os.environ.copy(); env.pop('FORKLIFT_FAILPOINT', None)
        p = subprocess.run([str(self.bin), '--home', str(home or self.source), '--pg-bin', str(self.pg),
                            '--json', *args], capture_output=True, text=True, env=env, timeout=300)
        obj = json.loads(p.stdout)
        self.commands.append({'args': args, 'home': str(home or self.source),
                              'seconds': time.monotonic()-t, 'response': obj})
        if p.returncode or not obj['ok']: raise RuntimeError(f'{args}: {obj}')
        return obj['result']

    def connection(self, name, home=None): return self.cli('connection', name, home=home)['connection']

    def sql(self, name, query, home=None):
        conn = self.connection(name, home)
        t = time.monotonic()
        p = subprocess.run([str(self.pg/'psql'), conn, '-XAt', '-v', 'ON_ERROR_STOP=1', '-c', query],
                           text=True, capture_output=True, timeout=300)
        self.queries.append({'branch': name, 'home': str(home or self.source), 'sql': query,
                             'seconds': time.monotonic()-t, 'result': p.stdout.strip(), 'exit': p.returncode})
        if p.returncode: raise RuntimeError(p.stderr)
        return p.stdout.strip()

    def digest(self, name, home=None):
        return self.sql(name, "SELECT count(*) || ':' || md5(string_agg(md5(row_to_json(a)::text), '' ORDER BY id)) FROM accounts a", home)

    def snapshot(self, title, home=None):
        view = self.cli('list', home=home)
        usage = process_usage(view)
        assert usage['clusters'] == view['active_clusters'] <= 3
        snapshot = {'title': title, 'tree': tree(view), 'view': view, 'resources': usage,
                    'storage': self.cli('stats', home=home)}
        self.snapshots.append(snapshot)
        print('\n'+title+'\n'+snapshot['tree'], flush=True)
        print(f"{usage['processes']} PostgreSQL processes; {usage['sum_rss_kib']/1024:.1f} MiB summed RSS (not unique RAM)", flush=True)

    def plans(self, name):
        query = 'SELECT sum(balance) FROM accounts WHERE balance < 100'
        plans = [json.loads(self.sql(name, 'EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) '+query))[0] for _ in range(5)]
        return {'query': query, 'result': self.sql(name, query), 'plans': plans,
                'median_execution_ms': statistics.median(p['Execution Time'] for p in plans)}

    def run(self):
        self.root.mkdir(parents=True, exist_ok=False)
        try:
            print(f'Demo: {self.root}\nSeeding {self.rows:,} rows', flush=True)
            self.summary['postgres'] = subprocess.check_output([str(self.pg/'postgres'), '--version'], text=True).strip()
            self.summary['free_bytes_at_start'] = shutil.disk_usage(self.root).free
            self.cli('init', 'source'); self.cli('start', 'source')
            self.summary['durability'] = self.sql('source', "SELECT current_setting('fsync'),current_setting('full_page_writes'),current_setting('synchronous_commit'),current_setting('data_checksums')")
            assert self.summary['durability'] == 'on|on|on|on'
            subprocess.run([str(self.pg/'psql'), self.connection('source'), '-X', '-v', 'ON_ERROR_STOP=1',
                            '-v', f'rows={self.rows}', '-f', str(REPO/'fixtures/seed.sql')], check=True, capture_output=True, timeout=300)
            self.summary['original_digest'] = self.digest('source')
            self.summary['database_bytes'] = int(self.sql('source', 'SELECT pg_database_size(current_database())'))
            self.cli('checkpoint', 'source', '--name', 'seed'); self.cli('stop', 'source')
            names = ['add-index', 'change-schema', 'delete-rows'] + [f'experiment-{i:02d}' for i in range(4,21)]
            for name in names: self.cli('branch', '--from', 'seed', '--name', name)
            self.snapshot('20 parked experiments + one parked source')
            for name in names[:3]: self.cli('start', name)
            self.summary['query_before'] = self.plans('add-index')
            self.sql('add-index', 'CREATE INDEX accounts_balance_idx ON accounts(balance); ANALYZE accounts')
            self.summary['query_after'] = self.plans('add-index')
            assert self.summary['query_before']['result'] == self.summary['query_after']['result']
            # Observe planner choice without disabling scans or forcing an index.
            self.summary['index_used'] = any('accounts_balance_idx' in json.dumps(p) for p in self.summary['query_after']['plans'])
            assert self.summary['index_used'], 'planner did not choose the demo index; inspect the recorded plans'
            self.sql('change-schema', "ALTER TABLE accounts ADD COLUMN experiment text NOT NULL DEFAULT 'schema-branch'")
            self.sql('delete-rows', 'DELETE FROM accounts WHERE id % 10 = 0')
            self.summary['deleted_branch_rows'] = int(self.sql('delete-rows', 'SELECT count(*) FROM accounts'))
            assert self.summary['deleted_branch_rows'] == self.rows-self.rows//10
            assert self.digest('add-index') == self.summary['original_digest']
            assert self.sql('add-index', "SELECT count(*) FROM information_schema.columns WHERE table_name='accounts' AND column_name='experiment'") == '0'
            assert self.sql('change-schema', "SELECT count(*) FROM accounts WHERE experiment='schema-branch'") == str(self.rows)
            self.snapshot('Three active experiments: index, schema change, and 10% deletion')
            self.cli('reset', 'delete-rows', '--to', 'seed', '--yes')
            assert self.digest('delete-rows') == self.summary['original_digest']
            assert self.sql('delete-rows', "SELECT to_regclass('accounts_balance_idx') IS NULL") == 't'
            self.summary['reset_verified'] = True
            self.cli('checkpoint', 'add-index', '--name', 'indexed')
            self.cli('export', 'indexed', '--output', str(self.root/'indexed.forklift'))
            for name in names[:3]: self.cli('stop', name)
            self.cli('start', 'source')
            assert self.digest('source') == self.summary['original_digest']
            assert self.sql('source', "SELECT to_regclass('accounts_balance_idx') IS NULL") == 't'
            assert self.sql('source', "SELECT count(*) FROM information_schema.columns WHERE table_name='accounts' AND column_name='experiment'") == '0'
            self.cli('stop', 'source'); self.summary['source_unchanged'] = True
            # Remove the source store from its recorded location. Restore can only
            # consume the self-contained exported directory.
            self.source.rename(self.root/'source-offline')
            self.cli('restore', str(self.root/'indexed.forklift'), '--name', 'restored-index', home=self.target)
            self.cli('start', 'restored-index', home=self.target)
            assert self.digest('restored-index', self.target) == self.summary['original_digest']
            assert self.sql('restored-index', "SELECT to_regclass('accounts_balance_idx') IS NOT NULL", self.target) == 't'
            self.summary['restored_without_source_verified'] = True
            self.snapshot('Export restored independently, including the saved index', self.target)
            self.cli('stop', 'restored-index', home=self.target)
            (self.root/'source-offline').rename(self.source)
            self.snapshot('Completed: original branches parked')
            self.snapshot('Completed: restored branch parked', self.target)
            self.summary['export_bytes'] = sum(p.stat().st_size for p in (self.root/'indexed.forklift').rglob('*') if p.is_file())
            assert not list(self.root.rglob('postmaster.pid'))
            self.summary['status'] = 'passed'
        except BaseException as e:
            self.summary['status'] = 'failed'; self.summary['error'] = str(e)
            raise
        finally:
            # Only use Forklift's identity-checked lifecycle commands for cleanup.
            self.summary['cleanup_errors'] = []
            for store in [self.source, self.root/'source-offline', self.target]:
                if not (store/'catalog.json').exists(): continue
                try:
                    view = self.cli('list', home=store)
                    for b in view['branches']:
                        if b['activity'] == 'running': self.cli('stop', b['name'], home=store)
                except Exception as e: self.summary['cleanup_errors'].append(str(e))
            if self.summary['cleanup_errors']: self.summary['status'] = 'failed'
            self.write_report()
        if self.summary['status'] != 'passed': raise RuntimeError('cleanup failed; inspect summary.json')
        (self.root/'SUCCESS').write_text('20-branch demo: isolation, index plan, reset and independent restore passed\n')
        print(f'PASS: {self.root}/report.md', flush=True)

    def write_report(self):
        for name, value in [('summary', self.summary), ('snapshots', self.snapshots)]:
            (self.root/(name+'.json')).write_text(json.dumps(value, indent=2)+'\n')
        for name, values in [('commands', self.commands), ('queries', self.queries)]:
            (self.root/(name+'.jsonl')).write_text(''.join(json.dumps(v,separators=(',',':'))+'\n' for v in values))
        lines = ['# Forklift demo results', '', f"Status: **{self.summary['status']}**. Rows: {self.rows:,}.", '',
                 'Measurements come from this run. Filesystem cache is uncontrolled. RSS can count shared pages repeatedly; file bytes and block counts are not unique APFS disk usage.', '']
        if 'query_after' in self.summary:
            lines += ['## Query plan experiment', '',
                      'Five EXPLAIN ANALYZE samples before and after creating the index; unchanged query result. Fixed order, no forced planner settings. This is a SQL index effect, not a Forklift storage speedup.', '',
                      '| Phase | Median PostgreSQL execution time |', '| --- | ---: |']
            for key,label in [('query_before','Before index'),('query_after','After index')]:
                lines.append(f"| {label} | {self.summary[key]['median_execution_ms']:.3f} ms |")
            lines += ['', 'Full plans, buffer counts and timings: [summary.json](summary.json).', '']
        lines += ['## Selected lifecycle operations', '', '| Operation | Seconds (whole CLI) |', '| --- | ---: |']
        for r in self.commands:
            if r['args'][0] in ('checkpoint','reset','export','restore'):
                lines.append(f"| {' '.join(r['args'][:2])} | {r['seconds']:.3f} |")
        for snap in self.snapshots:
            u=snap['resources']; total=snap['storage']['total']
            lines += ['', '## '+snap['title'], '', '```text',snap['tree'],'```','',
                      f"Observed: {u['clusters']} clusters, {u['processes']} PostgreSQL processes, {u['sum_rss_kib']/1024:.1f} MiB summed RSS.",
                      f"Store apparent bytes: {total['apparent_bytes']:,}; allocated block bytes (not unique): {total['allocated_not_unique_bytes']:,}."]
        lines += ['', '## Evidence and limits', '',
                  '[Commands](commands.jsonl) · [SQL results](queries.jsonl) · [Snapshots and storage](snapshots.json)', '',
                  'Source/cache objects, retained generations and WAL are counted in store totals. Export is separate. No exact shared-space or peak-space measurement; no power-loss or competing-product benchmark. The earlier 100-branch benchmark is a separate run.', '',
                  'Snapshots show observed state at each stage, not a live dashboard. Use `python3 scripts/demo.py --inspect RUN_DIRECTORY` for current state.', '']
        (self.root/'report.md').write_text('\n'.join(lines))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=REPO/'benchmark-data/demo')
    parser.add_argument('--rows',type=int,default=1000000)
    parser.add_argument('--inspect',type=Path,help='show current branch tree and process resources for an existing demo')
    parser.add_argument('--binary',type=Path,default=Path(os.environ.get('FORKLIFT_BIN', REPO/'target/release/forklift')))
    parser.add_argument('--pg-bin',type=Path,default=Path(os.environ.get('PG_BIN','/opt/homebrew/opt/postgresql@17/bin')))
    args=parser.parse_args()
    if args.inspect:
        if not any((args.inspect/name/'catalog.json').exists() for name in ('source','source-offline','restored')):
            parser.error('--inspect must identify an existing demo directory')
        for name in ('source','source-offline','restored'):
            store=args.inspect.resolve()/name
            if not (store/'catalog.json').exists(): continue
            p=subprocess.run([str(args.binary.resolve()),'--home',str(store),'--json','list'],capture_output=True,text=True,check=True)
            view=json.loads(p.stdout)['result'];print(str(store)+'\n'+tree(view));print(json.dumps(process_usage(view)))
        return
    if not 1000 <= args.rows <= 1000000: parser.error('--rows must be 1000..1000000')
    if args.output.exists(): parser.error('--output must be a new directory')
    Demo(args).run()


if __name__=='__main__': main()
