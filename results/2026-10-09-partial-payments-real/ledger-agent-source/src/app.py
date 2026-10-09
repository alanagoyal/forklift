"""Tiny invoicing application; JSON output is its application-level interface."""
import json
import os
import subprocess
import sys


REPORT_SQL = """
SELECT json_build_object(
    'version', 2,
    'invoices', COALESCE(json_agg(i ORDER BY id), '[]'::json),
    'outstanding_cents', COALESCE(sum(balance_cents), 0)
)
FROM (
    SELECT i.id, i.customer, i.amount_cents,
           COALESCE(sum(p.amount_cents), 0) AS amount_paid_cents,
           i.amount_cents - COALESCE(sum(p.amount_cents), 0) AS balance_cents
    FROM invoices i LEFT JOIN payments p ON p.invoice_id = i.id
    GROUP BY i.id
) i
"""


def query(sql):
    return subprocess.check_output([os.environ['PG_BIN'] + '/psql', os.environ['DATABASE_URL'], '-XAtq', '-v', 'ON_ERROR_STOP=1', '-c', sql], text=True).strip()


def report():
    return json.loads(query(REPORT_SQL))


def record_payment(invoice_id, amount_cents):
    if amount_cents <= 0:
        raise ValueError('payment must be a positive integer number of cents')
    # Integer parsing at the CLI boundary makes these SQL literals safe.
    return json.loads(query(f"""
        BEGIN;
        INSERT INTO payments (invoice_id, amount_cents)
        VALUES ({int(invoice_id)}, {int(amount_cents)});
        {REPORT_SQL};
        COMMIT;
    """))


def validate_report(result):
    assert result['version'] == 2
    assert len(result['invoices']) == 3
    for invoice in result['invoices']:
        assert 0 <= invoice['amount_paid_cents'] <= invoice['amount_cents']
        assert invoice['balance_cents'] == invoice['amount_cents'] - invoice['amount_paid_cents']
    assert result['outstanding_cents'] == sum(i['balance_cents'] for i in result['invoices'])


if __name__ == '__main__':
    try:
        if len(sys.argv) == 1:
            result = report()
        elif len(sys.argv) == 4 and sys.argv[1] == 'record-payment':
            result = record_payment(int(sys.argv[2]), int(sys.argv[3]))
        else:
            raise ValueError('usage: python3 src/app.py [record-payment INVOICE_ID AMOUNT_CENTS]')
        validate_report(result)
    except (ValueError, subprocess.CalledProcessError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
    print(json.dumps(result, sort_keys=True))
