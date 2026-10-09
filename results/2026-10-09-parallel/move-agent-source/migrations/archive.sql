BEGIN;

CREATE TABLE invoice_archive (LIKE invoices INCLUDING ALL);

WITH archived AS (
    INSERT INTO invoice_archive (id, customer, amount_cents, paid)
    SELECT id, customer, amount_cents, paid
    FROM invoices
    WHERE id = 3 AND paid
    RETURNING id
)
DELETE FROM invoices
USING archived
WHERE invoices.id = archived.id;

COMMIT;
