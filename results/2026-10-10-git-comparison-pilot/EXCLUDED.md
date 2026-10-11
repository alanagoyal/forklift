# Excluded partial run

Interrupted after part of the 8 MiB Forklift workload, before other methods ran.
This harness revision did not yet explicitly sync newly reconstructed physical
PGDATA before starting PostgreSQL or new LFS objects before acknowledging capture.
Those barriers were added before the final run. No values here are pooled into
the final report. Cleanup succeeded with no remaining postmaster.pid files.
