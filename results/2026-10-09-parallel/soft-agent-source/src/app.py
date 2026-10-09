"""Tiny invoicing application; JSON output is its application-level interface."""
import json
import os
import subprocess


def query(sql):
    return subprocess.check_output([os.environ['PG_BIN'] + '/psql', os.environ['DATABASE_URL'], '-XAt', '-v', 'ON_ERROR_STOP=1', '-c', sql], text=True).strip()


def report():
    data = json.loads(query("""
        SELECT json_build_object(
            'invoices', COALESCE((
                SELECT json_agg(i ORDER BY id)
                FROM (SELECT id, customer, amount_cents, paid
                      FROM invoices WHERE NOT archived) i
            ), '[]'::json),
            'archived_count', (SELECT count(*) FROM invoices WHERE archived)
        )
    """))
    invoices = data['invoices']
    return {'version': 2, 'invoices': invoices,
            'archived_count': data['archived_count'],
            'outstanding_cents': sum(i['amount_cents'] for i in invoices if not i['paid'])}


if __name__ == '__main__':
    result = report()
    assert result['outstanding_cents'] == 17500
    assert result['version'] == 2
    assert [i['id'] for i in result['invoices']] == [1, 2]
    assert result['archived_count'] == 1
    print(json.dumps(result, sort_keys=True))
