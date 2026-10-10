# Contributing to Forklift

Forklift is an experimental local development tool. Bug reports, documentation
improvements, and focused pull requests are welcome. Open an issue before a large
change so we can agree on its scope. Include your macOS, Rust and PostgreSQL
versions, the command you ran, and a small reproduction using disposable data.
Remove credentials, private source and database contents from reports and logs.

## Development setup

Use an Apple Silicon Mac with APFS, native PostgreSQL 17, Python 3 and Git.
Install Rust through rustup; `rust-toolchain.toml` selects the toolchain and tools.

```sh
brew install postgresql@17
export PATH="$HOME/.cargo/bin:$PATH"
cargo build --locked
cargo fmt --check
cargo test --locked
cargo clippy --locked --all-targets --all-features -- -D warnings
```

Real PostgreSQL tests are also required for storage or recovery changes. See the
[lifecycle tests](docs/lifecycle.md), [paired tests](docs/paired.md), and
[project tests](results/2026-10-08-project/report.md#reproduce) for commands.
These create fresh stores under ignored `benchmark-data/` and require local Unix
sockets and PostgreSQL shared memory. Do not start a shared Homebrew database
service for them. Never run tests against a production or personal database.

## Code map

- `src/bin/forklift.rs`: command-line arguments and user-facing output.
- `src/lifecycle.rs`: database branches, process identity, catalog and recovery.
- `src/objects.rs`, `src/cache.rs`, `src/storage.rs`: checkpoint packs, verified
  materializations and filesystem operations. PostgreSQL uses ordinary local files.
- `src/paired.rs`: selecting matching application-source and database generations.
- `src/project.rs`: applying selected source paths at a stable Git worktree path.
- `scripts/test_*.py`: independent checks against actual PostgreSQL and Git.
- `src/main.rs`: the historical feasibility benchmark, a separate executable.

## Change guidelines

Keep commits focused, with entirely lowercase subjects and bodies. Describe the
user-visible change and relevant validation in your pull request. Use plain
language when explaining Rust or storage behavior.

Keep `fsync`, `full_page_writes`, `synchronous_commit` and data checksums enabled.
Do not weaken process identity, file ownership or recovery checks to make tests
pass. Add a regression test for a correctness fix, including what happens after an
interruption when relevant. Distinguish implemented behavior from plans and measured
results. Process-exit tests do not establish arbitrary host power-loss safety.

Retain historical evidence as historical evidence; do not rewrite old reports to
claim a new run. Keep generated clusters, local configuration and credentials out
of commits. Retention/pruning is unsupported; test directories remain on disk.
Historical compatibility tests use specific Git commits, so keep full history
available when running them.

Contributions are provided under the project's [MIT license](LICENSE).
