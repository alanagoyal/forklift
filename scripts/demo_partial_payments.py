#!/usr/bin/env python3
"""Two real agents implement partial payments, then rewind only one design.

Reuses the archive demo's isolated fixture lifecycle and genuine Codex launcher.
The harness specifies interfaces and checks results; agents implement all feature
code and migrations. All monetary values are integer cents.
"""
import argparse
import json
from pathlib import Path
import shutil
import sys

import demo_parallel as parallel
from paired_support import inventory

NAMES = ('amount', 'ledger')
save = parallel.save


class Demo(parallel.Demo):
    names = NAMES

    def prepare(self):
        super().prepare()
        assert self.observe('amount') == self.observe('ledger')

    def write_prompts(self):
        common = '''Support partial payments in this isolated invoice application. Another real Codex agent is concurrently implementing the SAME feature using an incompatible design in another isolated worktree/database. Work only in this directory and injected DATABASE_URL/PG_BIN. Do not read/change sibling worktrees, Forklift metadata, configuration/hooks, Git index/history, notes.txt, src/local.txt, or .env.local. Do not commit or use forklift (its command gate is already held). Do not launch background processes. Keep PostgreSQL durability enabled. Actually edit src/app.py, create migrations/partial_payments.sql, execute that migration using psql ON_ERROR_STOP inside one transaction, and verify python3 src/app.py. Preserve all three invoices. All money is integer cents. Invoice 3 was fully paid before migration; preserve its paid amount of 9000 cents. Initial outstanding total stays 17500 cents. Implement JSON report version 2 with invoices containing id, customer, amount_cents, amount_paid_cents, balance_cents; compute outstanding_cents from actual balances. Implement python3 src/app.py record-payment INVOICE_ID AMOUNT_CENTS to transactionally record a positive payment, reject overpayment, and print the updated report. Ordinary python3 src/app.py prints report without mutations. Assertions must remain valid after partial payments. Verify by transaction rollback if testing mutations: do not leave any new payments behind, so invoice 1 still has balance 12500 when you finish. Explain your work.\n'''
        designs = {
            'amount': 'Design A: add invoices.amount_paid integer NOT NULL DEFAULT 0, measured in cents, with appropriate bounds. Backfill paid invoices. Use this cumulative amount directly for recording payments and computing balances. No payments table. Keep the existing paid boolean consistent with full payment.\n',
            'ledger': 'Design B: create a separate payments table with id primary key, invoice_id foreign key, amount_cents positive integer. Backfill a payment for already-paid invoice 3. Derive amount paid and remaining balance from individual payment rows. Do not add an amount_paid column to invoices. Keep paid boolean consistent with full payment.\n',
        }
        for name in NAMES:
            (self.evidence/f'{name}-prompt.txt').write_text(common+designs[name])

    def receipts(self, name):
        events = [json.loads(line) for line in (self.evidence/f'{name}-agent.jsonl').read_text().splitlines() if line.strip()]
        items = [event['item'] for event in events if event.get('type') == 'item.completed']
        assert any(i.get('type') == 'file_change' and i.get('status') == 'completed' and
                   any(c['path'].endswith('/src/app.py') for c in i.get('changes', [])) for i in items), f'{name}: missing app edit receipt'
        required = ['ALTER TABLE' if name == 'amount' else 'CREATE TABLE']
        assert any(i.get('type') == 'command_execution' and i.get('exit_code') == 0 and
                   'migrations/partial_payments.sql' in i.get('command', '') and
                   all(token in i.get('aggregated_output', '') for token in required) and
                   ('--single-transaction' in i.get('command', '') or
                    all(token in i.get('aggregated_output', '') for token in ('BEGIN', 'COMMIT'))) for i in items), f'{name}: missing successful migration receipt'

    def finish(self):
        events = json.loads((self.evidence/'agent-event-times.json').read_text())
        turns = {}
        for name in NAMES:
            selected = [e for e in events if e['agent'] == name]
            turns[name] = {'started_unix': next(e['observed_unix'] for e in selected if e['event'] == 'turn.started'),
                           'completed_unix': next(e['observed_unix'] for e in selected if e['event'] == 'turn.completed')}
            self.receipts(name)
        overlap = min(t['completed_unix'] for t in turns.values())-max(t['started_unix'] for t in turns.values())
        assert overlap > 0
        save(self.evidence/'provider-overlap.json', {'turns': turns, 'overlap_seconds': overlap,
             'clock': 'approximate event receipt wall-clock times, not model execution timings'})
        modified = {}
        for name in NAMES:
            observed = self.observe(name)
            assert observed['app']['version'] == 2 and observed['app']['outstanding_cents'] == 17500
            assert [i['balance_cents'] for i in observed['app']['invoices']] == [12500, 5000, 0]
            assert [i['amount_paid_cents'] for i in observed['app']['invoices']] == [0, 0, 9000]
            baseline = json.loads((self.evidence/f'{name}-baseline.json').read_text())
            assert all(observed[k] != baseline[k] for k in ('source', 'schema', 'data', 'app'))
            save(self.evidence/f'{name}-implemented.json', observed)
            shutil.copytree(self.root/name/'src', self.evidence/f'{name}-agent-source'/'src')
            shutil.copytree(self.root/name/'migrations', self.evidence/f'{name}-agent-source'/'migrations')
            (self.evidence/f'{name}-code.diff').write_text(self.git(self.root/name, 'diff', '--', 'src/app.py'))
            modified[name] = observed
        assert 'payments' not in modified['amount']['data']
        assert [i['amount_paid'] for i in modified['amount']['data']['invoices']] == [0, 0, 9000]
        assert all('amount_paid' not in i for i in modified['ledger']['data']['invoices'])
        assert len(modified['ledger']['data']['payments']) == 1
        payment = json.loads(self.project('ledger', 'run', '--', sys.executable, 'src/app.py', 'record-payment', '1', '2500'))
        assert payment['outstanding_cents'] == 15000
        assert payment['invoices'][0]['balance_cents'] == 10000
        save(self.evidence/'ledger-payment-output.json', payment)
        modified['ledger'] = self.observe('ledger')
        payments = modified['ledger']['data']['payments']
        assert len(payments) == 2 and sum(p['amount_cents'] for p in payments if p['invoice_id'] == 1) == 2500
        for name in NAMES:
            save(self.evidence/f'{name}-modified.json', modified[name])
            self.project(name, 'capture', 'experiment')
        before = self.inspect('ledger')
        self.project('amount', 'restore', 'baseline')
        restored, sibling = self.observe('amount'), self.observe('ledger')
        assert restored == json.loads((self.evidence/'amount-baseline.json').read_text())
        assert sibling == modified['ledger']
        assert self.inspect('ledger') == before
        save(self.evidence/'amount-restored.json', restored)
        save(self.evidence/'ledger-after-sibling-rewind.json', sibling)
        for name in NAMES:
            assert self.git_metadata(self.root/name) == json.loads((self.evidence/f'{name}-git-before.json').read_text())
            assert (self.root/name/'.env.local').read_text() == 'unselected local fixture setting\n'
            save(self.evidence/f'{name}-binding-after.json', self.inspect(name))
        control = self.control()
        assert control == json.loads((self.evidence/'control-before.json').read_text())
        save(self.evidence/'control-after.json', control)
        state = Path(self.inspect('amount')['state'])
        saved = json.loads((state/'pair/pairs/experiment.json').read_text())
        assert inventory(state/'pair'/saved['code']) == saved['files']
        assert self.source(state/'pair'/saved['code']) == modified['amount']['source']
        save(self.evidence/'retained-experiment.json', saved)
        for name in NAMES:
            self.hooks(name, 'teardown')
        self.cleanup()
        save(self.evidence/'summary.json', {'agents': 2, 'feature': 'partial payments',
            'design_a': 'invoices.amount_paid (integer cents)', 'design_b': 'payments table',
            'same_source_schema_rows_baseline': True, 'real_git_worktrees': 2,
            'independent_database_branches': True, 'restored': 'amount', 'sibling_unchanged': 'ledger',
            'seed_control_unchanged': True, 'exact_code_schema_rows_app_restored': True,
            'git_index_head_and_unselected_files_preserved': True, 'rejected_experiment_retained': True,
            'bb_hooks_executed': True, 'bb_native_provisioning_exercised': False,
            'baseline': restored['app'], 'amount_experiment': modified['amount']['app'],
            'ledger_after_payment_and_sibling_rewind': sibling['app'],
            'payment_cents': 2500, 'provider_turn_overlap_seconds': overlap})
        (self.evidence/'SUCCESS').write_text('real concurrent partial-payments agents; exact paired rewind; sibling payment/code/schema/rows/output preserved; seed/git unchanged; all servers stopped\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=['prepare', 'agents', 'finish', 'cleanup', 'all'])
    parser.add_argument('root', type=Path)
    parser.add_argument('evidence', type=Path)
    args = parser.parse_args()
    demo = Demo(args.root.resolve(), args.evidence.resolve())
    try:
        if args.phase == 'all':
            demo.prepare(); demo.agents(); demo.finish()
        else:
            getattr(demo, args.phase)()
    except BaseException:
        if demo.evidence.exists():
            demo.cleanup()
        raise
