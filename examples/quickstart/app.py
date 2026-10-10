"""A synchronous sample app; run through forklift project task report."""
import json
import os
import subprocess

VERSION = 1

rows = json.loads(subprocess.check_output([
    os.environ['PG_BIN'] + '/psql', os.environ['DATABASE_URL'], '-XAt',
    '-v', 'ON_ERROR_STOP=1', '-c',
    'SELECT json_agg(i ORDER BY id) FROM (SELECT id, customer, amount_cents, paid FROM invoices) i',
], text=True))
print(json.dumps({'version': VERSION, 'invoices': rows,
                  'outstanding_cents': sum(i['amount_cents'] for i in rows if not i['paid'])}))
