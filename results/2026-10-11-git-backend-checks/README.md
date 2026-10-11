# Implementation checks

All ten existing suites in `existing-regressions.json` passed on the first release
failpoint build. Final unit tests (18 tests across targets), Clippy with warnings
denied, Git-backend corruption/publication/retention/export tests, and the Git
selected-source project suite passed on the final implementation. `final-checks.json`
records commands, timings and binary identities. The final normal release build
has no failpoint feature and is the benchmark binary. Compilation and test runs
are excluded from benchmark timings.

The project suite covers two source layouts, staged/dirty files, Git index/HEAD
preservation, independent worktrees/databases, corruption, exclusion gates, and
capture/restore journal interruptions. The Git suite additionally checks mixed
backend history, catalogs lacking new fields, hostile Git environment/config,
corrupted blobs/manifests, stopped-source recovery, interrupted exports, interrupted
GC, pinned retention and packed restores. Full command records for these final
suites are compressed alongside the logs. Existing regression logs cover lifecycle,
objects, extensions, cache, cleanup, retention, paired workspaces, project onboarding,
managed applications, and the chunked project workflow. Native Supabase and historical
binary builds were not rerun; those storage paths are unchanged.

All fixture PostgreSQL servers were stopped; a recursive check of the owned
`benchmark-data` tree found no `postmaster.pid` after tests. Managed-app teardown
also passed its owned-process recovery/stop checks. Earlier sandbox-only attempts
could not allocate PostgreSQL shared memory and were rerun successfully with the
appropriate execution permission; they are not benchmark samples.
