-- No randomness, wall-clock timestamps, external extensions or network inputs.
CREATE TABLE accounts (
    id integer PRIMARY KEY,
    balance integer NOT NULL,
    cohort integer NOT NULL,
    payload text NOT NULL
);
INSERT INTO accounts
SELECT i, (i * 7919::bigint % 100000)::integer, i % 100,
       repeat(md5('forklift-v1:' || i::text), 8)
FROM generate_series(1, :rows) AS i;
CREATE INDEX accounts_cohort_idx ON accounts(cohort);
VACUUM (ANALYZE) accounts;
