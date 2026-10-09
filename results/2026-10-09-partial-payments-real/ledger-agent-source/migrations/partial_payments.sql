CREATE TABLE payments (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    invoice_id integer NOT NULL REFERENCES invoices(id),
    amount_cents integer NOT NULL CHECK (amount_cents > 0)
);

CREATE INDEX payments_invoice_id_idx ON payments (invoice_id);

-- Preserve the historical settlement before deriving paid from payment rows.
INSERT INTO payments (invoice_id, amount_cents)
SELECT id, amount_cents FROM invoices WHERE paid;

CREATE FUNCTION lock_payment_invoices() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        PERFORM id FROM invoices WHERE id = NEW.invoice_id FOR UPDATE;
    ELSIF TG_OP = 'DELETE' THEN
        PERFORM id FROM invoices WHERE id = OLD.invoice_id FOR UPDATE;
    ELSE
        PERFORM id FROM invoices
        WHERE id IN (OLD.invoice_id, NEW.invoice_id) ORDER BY id FOR UPDATE;
    END IF;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END;
$$;

CREATE FUNCTION sync_payment_invoices() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    affected_ids integer[];
    affected_id integer;
    total_paid bigint;
    invoice_amount integer;
BEGIN
    IF TG_OP = 'INSERT' THEN affected_ids := ARRAY[NEW.invoice_id];
    ELSIF TG_OP = 'DELETE' THEN affected_ids := ARRAY[OLD.invoice_id];
    ELSE affected_ids := ARRAY[OLD.invoice_id, NEW.invoice_id];
    END IF;
    FOREACH affected_id IN ARRAY affected_ids LOOP
        SELECT amount_cents INTO invoice_amount FROM invoices WHERE id = affected_id;
        SELECT COALESCE(sum(amount_cents), 0) INTO total_paid
        FROM payments WHERE invoice_id = affected_id;
        IF total_paid > invoice_amount THEN
            RAISE EXCEPTION 'payment exceeds remaining balance for invoice %', affected_id;
        END IF;
        UPDATE invoices SET paid = (total_paid = invoice_amount) WHERE id = affected_id;
    END LOOP;
    RETURN NULL;
END;
$$;

CREATE TRIGGER payments_lock BEFORE INSERT OR UPDATE OR DELETE ON payments
FOR EACH ROW EXECUTE FUNCTION lock_payment_invoices();

CREATE TRIGGER payments_sync AFTER INSERT OR UPDATE OR DELETE ON payments
FOR EACH ROW EXECUTE FUNCTION sync_payment_invoices();

UPDATE invoices i SET paid = (
    i.amount_cents = (SELECT COALESCE(sum(p.amount_cents), 0)
                     FROM payments p WHERE p.invoice_id = i.id)
);
