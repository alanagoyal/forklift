# milestone seven: native Rust paired rewind

> Historical document. Plans, commands, limitations and measurements describe
> the development stage recorded here. See the [history index](README.md) for
> context and links to current documentation.

Implemented and validated: paired application capture, restore, command gating,
and journal recovery now live in Rust and are exposed by `forklift pair`.
The Python production coordinator is retired. This is the same bounded local
experiment workflow established in milestone six, not a new storage engine or
an expanded application sandbox.

## Implementation

- `src/paired.rs` owns format-1 workspace metadata, source inventories, durable
  saved pairs, the command gate, preparation/publication, and journal recovery.
- `src/bin/forklift.rs` exposes `pair WORKSPACE init|capture|restore|recover|inspect|stop|run`.
- Pair operations call `Store` directly. The database store lock is released
  before an app or agent runs, while the workspace gate stays held and is
  inherited by the synchronous child. The child retains it if the parent dies.
- Source capture uses exclusive destination creation, SHA-256 inventories,
  synchronized files/directories, and the existing atomic JSON publication helper.
- Relative generation paths, metadata sizes/formats, links, source entry/file
  limits and pair names are validated. These guards do not authenticate metadata
  against another process owned by the same user.
- The original workspace, saved-pair and journal formats remain version 1.
  Existing Python-created workspaces need no conversion.

The command semantics and explicit cooperative-writer boundary are documented
in [paired workspaces](../paired.md). Persistent application processes still need a
pause adapter. External effects, arbitrary background writers, Git history/index,
and dependency installation are outside the captured state. There is no garbage
collection; previous work remains retained.

## Python's remaining role

`scripts/paired.py` is removed. `scripts/paired_support.py` is a test/demo helper:
it invokes the Rust CLI, reads JSON, and computes an independent source inventory.
It implements no production capture, restore, lock or recovery protocol.
`demo_paired.py` orchestrates actual provider work and assertions;
`test_paired.py` drives deterministic failure scenarios against the Rust binary.
`test_paired_legacy.py` loads the retired coordinator from its immutable Git
revision solely to exercise backward compatibility in fresh owned test storage.
The fixture application itself is still a small Python command-line app.

## Verified outcomes

A fresh actual Codex invocation edited `app.py`, wrote a migration, executed
`BEGIN / ALTER TABLE / UPDATE 1 / COMMIT`, and checked the modified application.
Rust paired rewind then restored exact baseline source hashes/modes, schema,
rows, and application JSON. Source/sibling branches remained unchanged. See
[the evidence and transcripts](../../results/2026-10-08-rust-paired/report.md).

Validation passed:

- Seven Rust unit tests (six library tests and one feasibility test), formatting,
  and Clippy for all targets/features with warnings denied.
- 44 recorded native paired command calls, including nine injected exits, plus
  child output/exit propagation and failed-child-launch checks.
- The existing lifecycle suite: 84 calls and six injected exits.
- Actual historical Python format-1 capture/restore compatibility and recovery
  from three historical interrupted capture/restore journals.
- A normal release ignores both pair and database failure-injection variables;
  child JSON is passed through without a parent envelope.

All test/demo-owned database servers were stopped. PostgreSQL fsync,
full-page writes, synchronous commits and page checksums stayed enabled.
Failure injections are process-exit tests, not host power-loss simulations.

Single observations on the three-invoice fixture: capture 0.851 seconds, database
interruption 0.671 seconds, and restore 5.446 seconds. These are not a controlled
Python-versus-Rust comparison. Filesystem cache state was uncontrolled, and the
older Python run had different concurrent work. No performance improvement is
claimed from these values.

## Reproduce

```sh
export PATH="$HOME/.cargo/bin:/opt/homebrew/opt/postgresql@17/bin:$PATH"
cargo fmt --check
cargo test --locked
cargo clippy --locked --all-targets --all-features -- -D warnings
cargo build --locked --release --features test-failpoints
FORKLIFT_BIN=target/release/forklift python3 scripts/test_paired.py
FORKLIFT_BIN=target/release/forklift python3 scripts/test_lifecycle.py
FORKLIFT_BIN=target/release/forklift python3 scripts/test_paired_legacy.py
cargo build --locked --release
python3 scripts/demo_paired.py prepare benchmark-data/my-rust-pair results/my-rust-pair
python3 scripts/demo_paired.py agent benchmark-data/my-rust-pair results/my-rust-pair
python3 scripts/demo_paired.py finish benchmark-data/my-rust-pair results/my-rust-pair
```

Use fresh output directories and a working Codex login. Integration tests need
native PostgreSQL shared memory and sockets. The legacy test requires historical
commit `36cf2322b00c5d81d372888864e15b946e7e0fb7` in the local Git history.
The agent is an external provider invocation; local database storage does not
mean that the coding model runs offline.
