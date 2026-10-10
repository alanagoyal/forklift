# Managed app lifecycle validation — 2026-10-10

Implemented behavior and the hook contract are documented in
[project.md](../../docs/project.md#opt-in-managed-app-lifecycle). This report records tests,
not a performance measurement or a claim that arbitrary agents can be paused.

The tests used a worktree based on
`147bca693cfb8bb216715070b6770fb0507e0d4d`. The host was macOS 26.6.2 ARM64, with Rust 1.95.0, Homebrew PostgreSQL 17.11,
and native Supabase CLI 2.120.0. All application/database fixtures were disposable.
The native tests cloned a downloaded artifact cache into private fixture state.
No hosted or unrelated project data/configuration was used.

## Results

- Formatting, 15 library unit tests plus the feasibility binary's unit test,
  and Clippy with all targets/features and warnings denied passed. These were
  rerun after the recovery changes.
- The contributor regression runner passed all 21 executed stages: lifecycle,
  objects/integrity, extensions, cache, cleanup, retention, paired workspaces,
  historical paired compatibility, project onboarding, managed apps, project
  restore, release checks, quickstart, and historical database compatibility,
  together with the builds/static checks.
- The real managed-app suite passed on both PostgreSQL and native Supabase.
  Its loopback HTTP server drained an in-flight request while a worker finished
  its database commit and corresponding source write. Capture and resume saw
  matching values; restore resumed with the saved values and fresh connection
  settings. The second worktree stayed running and was not restarted.
- Both backends passed stopped-app preservation, failed/partial start, failed
  stop, a stop hook that falsely returned success while status remained running,
  failed resume with the capture retained, unknown status, and refusal to adopt
  or stop an unowned running group.
- Concurrent commands were excluded while a hook ran. A timed-out hook retained
  the gate until it exited. Killing the Forklift parent during a delayed hook
  also left exclusion in place. The app processes were not killed or suspended.
- Interruptions after quiescence, after app restart, and at every existing
  backend restore fault boundary remained recoverable. Recovery left the app
  stopped and source/database values consistent. Repeated recovery succeeded.
- A focused follow-up on both backends verified that an unhealthy partial start
  could be drained using pinned hooks even after `.forklift.json` was edited.
- The existing native Supabase regression suite passed source/schema/data,
  Auth password/user and private Storage rewind; snapshot corruption/link
  refusal; six restore interruptions; retention; and independent worktrees.
  Its optional Booklist integration was deliberately not enabled.
- The new managed release check was run separately after the aggregate run.
  The normal release ignored managed pause/start failure-injection variables
  while a real app started, captured and restored successfully. It is now also
  wired into the contributor runner.

The managed suites asserted `fsync`, `full_page_writes`, `synchronous_commit`,
and data checksums were all enabled. Fixture app records were checked stopped
and fixture database pidfiles absent after teardown. The native regression
receipt also confirmed all stacks stopped and no hosted data used.

## Local evidence and reproduction

The original run retained ignored fixture receipts and logs under
`benchmark-data/`. Those local artifacts are not distributed with this report.
Rerun the suites below to produce fresh evidence for your checkout.

See [contributing](../../CONTRIBUTING.md#managed-application-lifecycle-checks) for
commands and required test binaries. `scripts/test_managed_release.py` takes a
successful PostgreSQL managed fixture directory and a normal release binary in
`FORKLIFT_BIN`. It adds one named test capture and stops the fixture afterwards.

## Limits of this evidence

Hooks are trusted to identify their own group and truthfully certify quiescence.
One group is an all-together lifecycle unit; independent service subsets are not
tracked. Unmanaged writers, coding agents, external effects and in-memory state
remain outside the guarantee. Process interruptions are tested; arbitrary host
power-loss recovery and checkpoint performance improvements are not claimed.
