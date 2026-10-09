ALTER TABLE invoices ADD COLUMN amount_paid integer NOT NULL DEFAULT 0;

UPDATE invoices SET amount_paid = amount_cents WHERE paid;

ALTER TABLE invoices
    ADD CONSTRAINT invoices_amount_paid_bounds
        CHECK (amount_paid >= 0 AND amount_paid <= amount_cents),
    ADD CONSTRAINT invoices_paid_consistent
        CHECK (paid = (amount_paid = amount_cents));
