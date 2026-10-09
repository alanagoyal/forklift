# Configurable project/worktree adapter

This milestone adds `forklift project` as a native Rust adapter over the paired
engine. There is no Booklist-specific code. Configuration supplies source ownership,
the database environment variable and argv commands. See [the workflow guide](../../docs/project.md).

## Implemented

- Machine-local seed registration shared by a repository's Git worktrees.
- A unique database binding for each worktree, with repeatable completed setup.
- Stable-path source capture/rewind, including selected dirty/untracked files and
  executable modes, while retaining pre-rewind source generations.
- Preservation of Git HEAD/index/history and files outside the selected paths.
- An outer restore journal that blocks managed commands during partial installation,
  verifies the chosen source inventory hash, and idempotently restores old or new
  source according to the paired engine's publication decision.
- Runtime injection of the selected PostgreSQL Unix-socket URL into a configured
  environment variable. No stale on-disk dotenv connection.
- Tracked BB hook templates; configuration-independent recovery and shutdown.
- Setup publication only after preparation succeeds; failed attempts remain retained.

## Validation

The deterministic integration suite creates two actual Git repositories and linked
worktrees with different source ownership and command configurations:

| Fixture | Owned source layout | Connection variable | Application |
| --- | --- | --- | --- |
| Python | `src/`, `migrations/`, `manifest.txt` | `DATABASE_URL` | Python invoking real psql |
| Shell | `tools/report.sh`, `db/schema/`, `manifest.txt` | `APP_DB` | Bash invoking real psql |

Both start with `v1 3`. The experiment edits application code, adds an `archived`
column, updates a row and deletes a row, producing `v2 2`. Paired restore returns
`v1 3`, all three rows, the original schema, original selected bytes/modes and dirty
manifest contents. Git's pointer file, HEAD and staged index remain identical.
Local environment files, dependency output and unselected siblings remain present.
Pre-rewind code remains in an older source generation. The seed and sibling
worktree's application/database remain unchanged.

Fourteen injected exits cover nine restore boundaries (before/after pair publication
and during source installation), three capture boundaries and setup before final
publication, plus an additional interrupted restore for the corruption check. Recovery chooses the expected old/new app output; repeated recovery
is safe. Additional checks cover corrupted recovery source, selected symlink refusal,
directory-to-file replacement, foreground command gate contention, idempotent setup,
and retained snapshots plus a fresh identity after removing/recreating a worktree.

The project driver records 171 subprocess calls (including Git and cleanup), with
14 exits injected. The initial SUCCESS summary omitted the extra corruption setup
exit; its count was corrected from the raw records.

The existing paired regression suite passes 44 recorded calls and nine injected
exits, including inherited child locking and core database interruptions. Rust's
eight unit tests, formatting and Clippy checks pass. The normal release is checked
separately for ignored test-failpoint environment variables and recovery/shutdown
with edited app configuration.

BB's tracked hooks are executed using `env bash` inside real linked Git worktrees,
with `BB_*` and `NODE_ENV` removed to match the documented hook environment. This
is hook-contract validation, not a run of BB's native provisioning service or a new
agent-authored experiment. The prior actual-agent experiment remains in the
[milestone-seven evidence](../2026-10-08-rust-paired/report.md).

## Limits

Three-row fixtures; no latency, physical-space or scale claims. PostgreSQL durability
is unchanged; no host power-loss test was performed. Agents and unmanaged writers
must be paused; foreground commands cooperate via the gate. Source installation is
recoverable across process interruption, not an atomic multi-file filesystem swap.

Compatibility remains local PostgreSQL 17, the `postgres` database, compatible
extensions and a client accepting the Unix-socket URL. The tested platform is macOS
ARM64/APFS. The npm config is illustrative, not a tested framework integration.
No hosted auth/storage adapter, TCP-only driver support, automatic agent pause,
background-process supervisor, database merging, retention policy or GC is added.

## Reproduce

```sh
cargo build --locked --release --features test-failpoints --bin forklift
FORKLIFT_BIN="$PWD/target/release/forklift" python3 scripts/test_project.py
FORKLIFT_BIN="$PWD/target/release/forklift" python3 scripts/test_paired.py
cargo build --locked --release --bin forklift
# Substitute the project fixture directory printed by test_project.py:
python3 scripts/test_project_release.py benchmark-data/project-EXAMPLE
cargo test
cargo fmt --check
cargo clippy --all-targets --all-features -- -D warnings
```

Test drivers require native PostgreSQL 17 binaries (default Homebrew path, overridable
with `PG_BIN`) and permission to run local PostgreSQL processes and Unix sockets.
Only test/demo orchestration uses Python; production commands use Rust.

## Evidence and cleanup

- [Project command receipts](project/records.json) and [success receipt](project/SUCCESS).
- [Project cleanup](project/cleanup.json): zero active clusters.
- [Normal-release checks](project/release-checks.json) and [final cleanup](project/release-cleanup.json).
- [Paired regression receipts](paired-regression/test-results.json) and [cleanup](paired-regression/cleanup.json).
- [Build/check summary](validation.txt).

All owned test servers were stopped, including those from intermediate runs. The
normal release binary is left in `target/release/forklift`. No project changes were
committed or pushed, and the separate learning site was not modified.
