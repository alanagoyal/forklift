# research and prior art

Reviewed 2026-10-07. These sources inform the plan; they do not validate Forklift's
unimplemented performance or establish that its proposed combination is unique.

- [Neon source layout](https://github.com/neondatabase/neon/blob/main/docs/sourcetree.md):
  Rust crates for services, C for Postgres and Postgres extensions, Python integration
  tests. Its pageserver handles page requests and WAL processing; a separate log
  service preserves incoming changes.
- [Neon local development and branching](https://github.com/neondatabase/neon#running-a-local-development-environment):
  workstation operation and local branch creation already exist. Running locally
  by itself is not a new feature we can claim.
- [Neon storage format](https://github.com/neondatabase/neon/blob/main/docs/pageserver-storage.md):
  immutable layer files and versioned pages underpin its storage architecture.
- [DBLab](https://v2.postgres.ai/docs/database-lab): existing Postgres thin-clone
  tooling using copy-on-write storage. Compare workflow and setup, not just naive
  copies. Check current release documentation before running it.
- [DBLab 4.2](https://postgres.ai/blog/20260917-dblab-engine-4-2-released):
  ongoing improvements include clone operations and sync; portability is also an
  existing area of work, not an uncontested novelty claim.
- [PostgreSQL filesystem backups](https://www.postgresql.org/docs/current/backup-file.html):
  ordinary filesystem copies require shutdown; correctly coordinated consistent
  snapshots and physical backup methods have additional rules. V1 chooses clean
  shutdown for an auditable starting point.

Questions for milestone one: exact APFS clone semantics and accounting on the
installed OS; performance cost of per-file cloning; native Postgres packaging;
local Neon setup requirements; and whether checkpoint portability provides enough
practical value to distinguish Forklift's workflow.
