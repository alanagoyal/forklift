"""Tiny invoicing application; JSON output is its application-level interface."""
import json
import os
import subprocess


def query(sql):
    return subprocess.check_output([os.environ['PG_BIN'] + '/psql', os.environ['DATABASE_URL'], '-XAt', '-v', 'ON_ERROR_STOP=1', '-c', sql], text=True).strip()


def report():
    invoices = json.loads(query("SELECT json_agg(i ORDER BY id) FROM (SELECT id, customer, amount_cents, paid FROM invoices) i"))
    return {'version': 1, 'invoices': invoices, 'outstanding_cents': sum(i['amount_cents'] for i in invoices if not i['paid'])}


if __name__ == '__main__':
    result = report()
    assert result['outstanding_cents'] == 17500
    assert len(result['invoices']) == 3
    print(json.dumps(result, sort_keys=True))
