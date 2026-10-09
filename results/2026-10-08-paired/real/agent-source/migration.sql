BEGIN;

ALTER TABLE invoices ADD COLUMN archived boolean NOT NULL DEFAULT false;
UPDATE invoices SET archived = true WHERE id = 3 AND paid = true;

COMMIT;
