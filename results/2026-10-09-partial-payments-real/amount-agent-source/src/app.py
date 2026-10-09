"""Tiny invoicing application; JSON output is its application-level interface."""
import json
import os
import subprocess
import sys


def query(sql):
    return subprocess.check_output([os.environ['PG_BIN'] + '/psql', os.environ['DATABASE_URL'], '-XqAt', '-v', 'ON_ERROR_STOP=1', '-c', sql], text=True).strip()


REPORT_SQL = """
SELECT COALESCE(json_agg(i ORDER BY id), '[]'::json)
FROM (SELECT id, customer, amount_cents, amount_paid AS amount_paid_cents,
             amount_cents - amount_paid AS balance_cents FROM invoices) i;
"""


def report():
    return make_report(query(REPORT_SQL))


def make_report(raw):
    invoices = json.loads(raw)
    for invoice in invoices:
        assert 0 <= invoice['amount_paid_cents'] <= invoice['amount_cents']
        assert invoice['balance_cents'] == invoice['amount_cents'] - invoice['amount_paid_cents']
    return {'version': 2, 'invoices': invoices,
            'outstanding_cents': sum(i['balance_cents'] for i in invoices)}


def record_payment(invoice_id, amount_cents):
    invoice_id, amount_cents = int(invoice_id), int(amount_cents)
    if amount_cents <= 0:
        raise ValueError('payment must be positive integer cents')
    # Integer conversion above makes these SQL literals safe. The row lock
    # serializes payments so the balance check sees the latest paid amount.
    sql = f"""
BEGIN;
DO $$
DECLARE remaining integer;
BEGIN
    SELECT amount_cents - amount_paid INTO remaining
    FROM invoices WHERE id = {invoice_id} FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'invoice not found';
    END IF;
    IF {amount_cents} > remaining THEN
        RAISE EXCEPTION 'payment exceeds invoice balance';
    END IF;
    UPDATE invoices SET amount_paid = amount_paid + {amount_cents},
        paid = (amount_paid + {amount_cents} = amount_cents)
    WHERE id = {invoice_id};
END $$;
{REPORT_SQL}
COMMIT;
"""
    return make_report(query(sql))


if __name__ == '__main__':
    try:
        if len(sys.argv) == 1:
            result = report()
        elif len(sys.argv) == 4 and sys.argv[1] == 'record-payment':
            result = record_payment(sys.argv[2], sys.argv[3])
        else:
            raise ValueError('usage: app.py [record-payment INVOICE_ID AMOUNT_CENTS]')
    except (ValueError, subprocess.CalledProcessError) as error:
        print(f'error: {error}', file=sys.stderr)
        sys.exit(1)
    print(json.dumps(result, sort_keys=True))
