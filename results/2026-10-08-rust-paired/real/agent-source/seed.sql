CREATE TABLE invoices (id integer PRIMARY KEY, customer text NOT NULL, amount_cents integer NOT NULL CHECK (amount_cents > 0), paid boolean NOT NULL DEFAULT false);
INSERT INTO invoices VALUES (1, 'Ada Studio', 12500, false), (2, 'Birch Labs', 5000, false), (3, 'Cedar Works', 9000, true);
