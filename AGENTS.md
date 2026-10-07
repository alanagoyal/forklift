# repository conventions

- All Git commit messages must be entirely lowercase, including subjects and bodies.
- Keep explanations accessible to someone learning Rust and database storage.
- Keep PostgreSQL's transaction and durability guarantees enabled. Never benchmark with fsync disabled as if it were equivalent durability.
- Distinguish planned behavior, implemented behavior, and measured results.
- Do not modify the separate greplcia project as part of Forklift work.
