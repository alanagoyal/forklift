# Ordinary Git storage in Forklift

Forklift supports two **implemented** PostgreSQL checkpoint stores: the existing
1 MiB chunk / zstd pack store (`chunked`, the default), and ordinary local Git
(`git`, experimental). Both use the same owned PostgreSQL lifecycle, version/build/extension
fingerprint checks, clean shutdown, branch generation publication, verified
materialization cache, connection selection, retention pins, and project/pair
journals. PostgreSQL still runs on ordinary materialized files, never in Git.

Select storage for future captures in an existing store:

```sh
forklift --home /absolute/private/store config --storage-backend git
forklift --home /absolute/private/store checkpoint main --name experiment
forklift --home /absolute/private/store config --storage-backend chunked
```

The selection persists, so `project capture` and `pair capture` use it when they
open that PostgreSQL store. Each checkpoint records its own backend; changing the
setting neither converts nor removes old checkpoints. Branch/reset/export find
the backend from the checkpoint, not the current setting. Existing stores without
the setting remain chunked. `list` includes both the setting and checkpoint backend;
`stats` includes `git_objects` when present. APFS/full copy selection is independent.

Selecting Git advances the catalog to format 6. Older binaries refuse that format
rather than accidentally treating Git snapshots as chunked snapshots. New binaries
still read formats 1–5. To return to an old binary, retain the original store or
export snapshots to version-one bundles and restore them into a separate store;
do not edit catalog version numbers or delete either repository. The pre-change
commit `af9b513657965a8a1ebf472b0dd89a3ac2b7f72d` remains a source rollback point.

## Representation and boundaries

`store/git-store/repo` is a private **bare Git repository**: blobs hold complete
PostgreSQL files, trees record their paths under `data/`, and each checkpoint has
a parentless commit at `refs/checkpoints/<manifest-sha256>`. Git can delta-compress
these whole files during explicit packing. There is no Git LFS, remote, upload,
application commit, or database working copy inside this repository. Parentless
commits let retention forget one snapshot without pinning its ancestors.

Format-two manifests record every file's length, SHA-256 and Git blob identifier,
all empty directories, and the same runtime compatibility evidence as format one.
The manifest's SHA-256 remains the checkpoint/cache identifier. A copy in the Git
tree must match the external manifest; every tree entry must match exactly. File
bytes are streamed from `cat-file` and verified against their recorded SHA-256
before use. Directory/file creation retains Forklift's existing private 0700/0600
policy, rather than relying on Git's limited Unix-mode support. Paths, symlinks,
hardlinks, tablespaces, recovery markers, size limits, and required cluster files
are checked with the shared validator. The lifecycle verifies clean `pg_control`
and owned-server identity before capture; the storage layer also refuses pidfiles.

Commands use a cleared environment and fixed Git executable location
(`/opt/homebrew/bin/git`, or `/usr/bin/git`). System/global config and attributes
are disabled. The local config and HEAD must match fixed bytes before any Git
subprocess runs; links, alternates, grafts, shallow repositories, replacement or
other unexpected refs, hooks directories, and additional worktrees are refused.
Plumbing commands bypass filters, disable hooks and replacement objects, and deny
protocols. There is no `checkout`, `add`, `commit`, network command, or shell
expansion. Application Git configuration does not control database storage.
Same-user concurrent tampering while a command runs is outside the threat model,
as with the existing store filesystem. Keep the store private.

## Publication, recovery, and retention

Capture stops the owned cluster cleanly, hashes its complete files, writes Git
objects, builds the tree/commit, and explicitly syncs **all** object files and
object directories before updating the checkpoint ref. Git also uses
`core.fsync=committed` and `core.fsyncMethod=fsync`. Ref files/directories and the
repository directory are synced before a verified manifest is written to a
synced temporary file, renamed, and its parent synced. Only then does the shared
catalog publish the checkpoint as ready. Re-reading all blobs before manifest
publication rejects corrupt objects that Git might otherwise reuse.

The shared lifecycle restarts a previously running source, or keeps a previously
stopped source stopped. Abrupt CLI death before catalog publication leaves an
unusable failed checkpoint and possibly unreachable objects/ref/manifest; later
reconciliation restores the recorded source-running state. A ready checkpoint
never references objects intentionally pruned by GC. Restore uses a new owned
generation, per-file fsync and directory sync exactly like chunked restore, and
switches the selected branch only after validation. Cache hits still hash all
files. Tests inject process exits at object/ref/manifest publication and restore
writes; they are **not power-loss tests**. These barriers do not establish equal
power-loss behavior on every filesystem or device.

`gc` previews dead Git snapshot refs; `gc --yes` removes unretained manifests and
refs, syncs ref removal, then runs ordinary `git gc --prune=now` and syncs the
repository. The catalog lock excludes other Forklift writers. Packing is explicit,
never hidden in capture. All live catalog checkpoints and locally present imported
origin manifests are roots. Shared branch/pair pins still block checkpoint removal.
`cleanup --yes` retains its existing generation/cache behavior. Git GC additionally
removes safe interrupted `.index-*` and `.export-*` staging entries. A process killed
during first repository initialization can leave `.git-store-<random>` beside the
store's repository; that unpublished directory is deliberately retained for manual
inspection/removal while no command is running. Unknown files, including a Git `.index-<id>.lock` left by a process killed
inside an index write, are retained rather than guessed safe to delete. Inspect
for surviving owned Git subprocesses before manually removing lock/staging files. Preview does not estimate Git's future packed size.

Portable exports from Git are implemented by verified materialization followed by
conversion to the existing format-one chunked bundle. Imports therefore work with
both current backends and older bundle readers, but the exported manifest ID is a
new chunked identity. This conversion costs extra CPU, I/O, and temporary space;
it is not a native Git bundle or an in-place migration. Interrupted exports may
leave staging until GC. External exports are never deleted by store GC.

## Scope and measured evidence

Native Supabase uses its separate filesystem snapshot/lifecycle path; this setting
only configures local PostgreSQL stores. Git storage of native Supabase volumes,
Docker/service images, Storage files, remote databases, cross-platform PostgreSQL
restores, live online physical capture, replication/tablespaces and Git remotes are
not implemented here. Selected unfinished application source continues to use
Forklift's existing project/pair source snapshots and journals. Changing database
storage does not create commits in the application's repository.

Run the backend and selected-source regressions with a failpoint build:

```sh
cargo build --locked --release --features test-failpoints --bin forklift
FORKLIFT_BIN=target/release/forklift python3 scripts/test_git_backend.py
FORKLIFT_BIN=target/release/forklift FORKLIFT_STORAGE_BACKEND=git python3 scripts/test_project.py
```

For measurements, rebuild **without** failpoints before timing:

```sh
cargo build --locked --release --bin forklift
python3 scripts/benchmark_backends.py \
  --root benchmark-data/integrated-backends \
  --output results/2026-10-11-integrated-backends \
  --sizes-mib 8,64,256 --samples 3
```

This reuses the deterministic fixture/verification workload of
[the earlier comparison](git-benchmark.md), now using Forklift for **both** storage
backends. It adds warm restore after each first restore, explicit cache-event
receipts, backend object/total accounting, PostgreSQL executable hashes and stopped
page-checksum checks. Fixture setup and builds are outside timings. Source commits
in this benchmark are harness-only state identifiers: source is coordinated
externally, not timed through the project journal. The project regression above
covers actual dirty/staged selected source, Git metadata preservation and journal
recovery separately; it is not a performance measurement.

Each size/backend retains ten snapshots: initial plus three unchanged, three
10-row/source edits, and three cumulative schema/index/source edits. Every state
is restored twice and independently provisioned into a second worktree/database;
source/schema/rows/invoice CLI/durability and two-way isolation are checked.
No OS cache is purged; 'first restore' means the first request in this harness,
not necessarily a cache miss (receipts identify actual cache builds/hits). Method
order reverses at 64 MiB. There is no persistent app server or HTTP-readiness timing.

Raw commands, events, samples, per-state fingerprints, environment and accounting
are retained. File lengths and `st_blocks*512` are accounting observations, **not
unique APFS physical space**. Shared clone blocks, sparse files and filesystem
metadata prevent that inference. Maintenance is separate; snapshot objects and
whole-store totals are reported before/after it. Git's conservative full-history
validation/fsync scans and double-read capture are implementation costs, not inherent
limits of Git. Git capture's legacy chunk/pack metric fields are zero/not applicable;
use its backend tag, scanned bytes, total duration and timing spans, not those fields
as deduplication evidence. Future optimization should preserve the publication and
integrity contract and be measured for both backends.

Generate median/range tables and validate raw receipts:

```sh
python3 scripts/summarize_backends.py results/2026-10-11-integrated-backends
```
