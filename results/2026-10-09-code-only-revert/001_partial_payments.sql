ALTER TABLE invoices ADD COLUMN amount_paid_cents integer;

UPDATE invoices
SET amount_paid_cents = CASE WHEN paid THEN amount_cents ELSE 0 END;

ALTER TABLE invoices ALTER COLUMN amount_paid_cents SET NOT NULL;
ALTER TABLE invoices ALTER COLUMN amount_paid_cents SET DEFAULT 0;
ALTER TABLE invoices DROP COLUMN paid;
