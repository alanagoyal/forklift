BEGIN;
ALTER TABLE accounts ADD COLUMN experiment text NOT NULL DEFAULT 'branch-a';
UPDATE accounts SET balance = balance + 7 WHERE id % 100 = 0;
CREATE INDEX accounts_balance_idx ON accounts(balance);
COMMIT;
