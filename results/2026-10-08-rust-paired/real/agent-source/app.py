"""Tiny invoicing application; JSON output is its application-level interface."""
import json
import os
import subprocess


def query(sql):
    return subprocess.check_output([os.environ['PG_BIN'] + '/psql', os.environ['DATABASE_URL'], '-XAt', '-v', 'ON_ERROR_STOP=1', '-c', sql], text=True).strip()


def report():
    invoices = json.loads(query("SELECT COALESCE(json_agg(i ORDER BY id), '[]'::json) FROM (SELECT id, customer, amount_cents, paid FROM invoices WHERE NOT archived) i"))
    archived_count = int(query("SELECT count(*) FROM invoices WHERE archived"))
    return {'version': 2, 'invoices': invoices, 'archived_count': archived_count, 'outstanding_cents': sum(i['amount_cents'] for i in invoices if not i['paid'])}


if __name__ == '__main__':
    result = report()
    assert result['version'] == 2
    assert result['outstanding_cents'] == 17500
    assert len(result['invoices']) == 2
    assert [i['id'] for i in result['invoices']] == [1, 2]
    assert result['archived_count'] == 1
    print(json.dumps(result, sort_keys=True))
