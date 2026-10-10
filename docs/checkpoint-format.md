# checkpoint object format, version 1

This guide describes the PostgreSQL backend. The optional [Supabase backend](supabase-native.md)
uses a separate stopped-filesystem snapshot format and runtime fingerprint.

Implemented in milestone three. This is a local object repository: no cloud
service, network connection, or new PostgreSQL storage engine is involved.
Running clusters still use ordinary files. Objects store saved, stopped state.

## Layout and identities

```text
STORE/
  catalog.json                    # catalog version 5
  generations/                    # running/stopped/old/failed cluster files
  cache/<manifest-sha256>/         # verified stopped materialization, entry.json + data/
  objects/
    manifests/<sha256>.json
    packs/<sha256>.pack
```

The catalog selects a checkpoint by its manifest's SHA-256 digest. The digest is
computed over the exact UTF-8 JSON bytes. The manifest contains:

- Format version, 1 MiB chunk size and 4 MiB maximum uncompressed pack size.
- PostgreSQL version, major version, platform/architecture and build configuration.
- The installed supported extensions, with database, name and version. Bundled
  extensions also record an installation SHA-256 fingerprint (described below).
- Required directories and files with safe relative paths, byte lengths, file
  SHA-256 checksums and ordered chunk hashes.
- A map from each chunk hash to its pack hash, byte offset and length.

Files are split into fixed-size chunks, with a possibly shorter final chunk.
Empty files have no chunks and the standard SHA-256 digest of empty input.
Files do not share chunk boundaries. Identical chunks are reused within a
checkpoint and across previous manifests, including complete orphan manifests
left after an interrupted catalog publication. Existing referenced data is
verified before reuse; corruption is an error, not silently overwritten.

New chunks are gathered into packs up to 4 MiB. A pack's name is the SHA-256 of
its **uncompressed** bytes. Its on-disk encoding is:

| Bytes | Meaning |
| --- | --- |
| 0–7 | ASCII magic `FLPK0001` |
| 8–15 | Uncompressed byte length, unsigned 64-bit little-endian |
| 16 onward | Zstandard-compressed pack data, compression level 3 when written |

Packing amortizes file and sync overhead for small PostgreSQL files. Chunk
locations inside packs are recorded in the manifest. A small read cache retains
at most four decoded packs (16 MiB of cached data); temporary buffers, manifests
and other program allocations consume additional memory. Compression is not
used as an excuse to turn off PostgreSQL durability.

This format records data contents and paths, with canonical private permissions:
restored directories are 0700 and files are 0600. Original mtimes, ownership,
ACLs and arbitrary source permission modes are not reproduced. The runtime file
`postmaster.opts` is omitted because it contains source-specific startup options;
Forklift supplies new options when it starts the restored cluster.

## Publication and interruption

A checkpoint checks supported extensions/configuration, cleanly stops its source,
then scans that owned cluster. Pack data is written to new temporary files,
synced, and atomically renamed without replacing existing files. The containing
directory is synced. Only after all packs are durable is the manifest published
using the same rules; only then can the catalog mark the checkpoint ready.

Each object is immutable. New work never edits an older pack or manifest. Failed
writes may retain partial temporary files, complete orphan packs, or a complete
manifest not selected by a ready checkpoint. These remain included in storage
accounting until explicit `forklift gc --yes` removes unreachable complete objects.
GC traces retained checkpoint manifests and branch origins before deleting packs;
see [the lifecycle guide](lifecycle.md).

The source resumes if it was previously running. A previously stopped source is
briefly started to inspect extension metadata, then stopped again. The catalog
records that intended state before inspection, so an interrupted inspection is
also reconciled back to stopped. Custom extensions and manual changes to the
managed PostgreSQL installation/configuration remain unsupported; do not install
extensions concurrently with checkpoint capture.

## Export and restore

`forklift export SAVE --output seed.forklift` produces a **directory**, not an
archive with hidden extraction behavior:

```text
seed.forklift/
  manifest.json
  manifest.sha256
  packs/<sha256>.pack
```

It contains exactly the complete pack set reachable from the selected manifest.
A reused pack can contain additional chunks not referenced by that manifest;
the complete pack is necessary to verify its content hash. Export does not copy
unrelated packs or manifests from the rest of the repository.

Export verifies packs, builds a private sibling staging directory, syncs all
files/directories, then atomically publishes the final directory without replacing
an existing destination. Export must be outside the managed store. An interrupted
export leaves the final path absent and may leave a `.forklift-export-*` sibling.
Those staging directories consume disk space and are not covered by a different
store's `stats` command.

`forklift --home NEW_STORE restore seed.forklift --name restored` creates a new,
stopped branch. It works without the source cluster or source object repository.
The export directory is its only data input. All output is materialized into a
fresh, owned generation; restore does not start a server or import a checkpoint
entry into the destination catalog. Its origin manifest hash is recorded on the
branch. Once restored, the branch does not depend on retaining the export.

Before creating data files, restore verifies the manifest digest, compatibility,
path topology, sizes and references. During materialization it bounds decoding,
checks complete pack hashes, chunk slices/hashes and complete file hashes. It
syncs files/directories, verifies clean PostgreSQL shutdown state and runs
`pg_checksums --check` before publishing the branch as stopped. Errors and
interruptions leave an unselectable failed generation, not a ready branch.
Previously valid checkpoints and branches are untouched.

SHA-256 detects corruption; it is not a signature proving who produced a bundle.
Only start PostgreSQL from trusted exports. Database files and configuration are
not a sandbox for untrusted code, just as a logical SQL dump is not one.

## Compatibility and limits

The current supported path is macOS ARM64 and PostgreSQL 17. Restore requires
an exact match of PostgreSQL's reported version and `pg_config --configure`
output, as well as OS/architecture. This intentionally includes the minor/build
version and may reject installations with different build paths. It is narrower
than “any PostgreSQL 17 server.” Portability was tested between independent stores
on the same Mac, not between different operating systems or machines.

Supported extensions are `plpgsql` 1.0, `citext` 1.6 (case-insensitive text),
`hstore` 1.8 (key/value columns), `uuid-ossp` 1.1 (UUID generation), `pg_trgm` 1.6
(text similarity), and `vector` 0.8.0 (pgvector embedding search). Install pgvector
separately against the pinned PostgreSQL installation; it is optional for projects
that do not use it. Other extensions, including `pgcrypto`, remain unsupported.
These modules are checked with `/usr/bin/otool` to depend only on macOS's system C
library; `pgcrypto` adds
an external OpenSSL dependency that is outside this compatibility policy.

Capture checks each installed version against PostgreSQL's available extension
versions and refuses extension C functions pointing outside their expected
`$libdir/<extension-name>` library. For each added extension, the manifest records
`installation_sha256`: a SHA-256 fingerprint of its local control file, all bundled
SQL install/upgrade scripts, and dynamic library. Files are sorted by filename;
the fingerprint hashes each filename, a NUL separator, and that file's hexadecimal
SHA-256 digest. Branch, reset, export and restore require the same fingerprint
from the destination PostgreSQL installation. Missing files, changed bytes,
unsupported dependencies, missing fingerprints and unsupported versions fail
closed. The runtime installation evidence is never loaded from the manifest.
Older `plpgsql`-only version-one manifests remain valid without this new field;
older Forklift binaries reject manifests containing the added extensions.

These checks cover trusted, unmodified PostgreSQL packages, not arbitrary custom
extension installations. They do not prove that a user has left SQL function
bodies untouched or that OS libraries are byte-identical. The export still needs
a matching local PostgreSQL installation; it does not bundle extension binaries.
Real PostgreSQL tests exercise case-insensitive comparison, hstore values, UUID
functions, multiple databases, branch/reset, portable restore and rejected
metadata/library changes. These are functional checks, not performance results.

Metadata covers connectable databases; untouched template0 is assumed to be the
standard initdb template.
Nonconnectable user databases, external tablespaces and preloaded libraries are
rejected. All clusters created by the CLI use PostgreSQL page checksums.

Limits bound processing: 32 MiB manifests, 100,000 file/directory entries,
100,000 indexed chunk hashes, 1 TiB total manifest file lengths, path length
512 bytes and depth 16. These are guards, not tested capacity claims; more
restrictive combinations can apply. Paths use ASCII letters/digits, `_`, `-`,
`.` and component separators. Absolute paths, empty/dot/dot-dot components,
case-folded aliases, missing parents, symlinks, hard-linked input objects and
special files are rejected. Decoder input/output sizes are bounded. Required
PostgreSQL paths must exist; live-process/recovery marker files are forbidden.

## Catalog upgrade and native clones

Catalog versions 1–4 are atomically upgraded to version 5 on first use. Version
5 also records optional authenticated TCP endpoints and coordinator ownership for
safe retention. Earlier binaries reject this version so they cannot silently bypass
those checks. The running-cluster limit still defaults to 3. The object format stays
at 1. Legacy
physical checkpoints remain available for branch/reset. The old milestone-two
binary rejects the upgraded catalog; downgrading it manually is unsupported.
A legacy checkpoint can be converted by branching from it and taking a new
checkpoint. Export returns `legacy_checkpoint` with those instructions until then.

New checkpoints remain object-backed. Branch and reset now use a disposable
materialized cache at `cache/<manifest-sha256>/data`. The first use reconstructs
and verifies objects into a `.building-*` directory, checks clean shutdown,
records ownership in `entry.json` (format 1, store ID, manifest ID), then syncs and
atomically publishes the directory without overwriting an existing entry.

Every cache hit verifies ownership, the exact file/directory set, each file's
length and SHA-256, and clean PostgreSQL shutdown. A cache pidfile causes a refusal;
Forklift never starts or signals the cache. Unsafe links and ownership mismatches
fail closed. Ordinary content corruption moves the entry to `.invalid-*` and
rebuilds it from objects. Interrupted staging and quarantines remain counted in
storage until explicitly removed with `forklift cleanup --yes`; run `forklift cleanup`
first to preview eligible files. No automatic cleanup runs. See
[cleanup and recovery](lifecycle.md#saved-state-and-recovery).

Verified cache files are APFS-cloned into a new branch generation, then every file
is synchronized before directories and catalog publication. Up to eight file-sync
workers overlap independent calls; no durability barrier is removed. Full copies
are available with `--copy-mode full`. Cache hits hash the complete materialization,
so they are not constant-time forks. The resulting branch is checked for
ownership, PostgreSQL version and clean shutdown before publication; it is not
hashed a second time after cloning. Portable restore also runs `pg_checksums`.

`--no-cache` selects direct object reconstruction. Actual JSON stages distinguish
`cache_hit_verify`, `cache_build_verify_publish`, `cache_apfs_clone_and_sync`,
`cache_full_copy_and_sync`, and `object_materialization`. The JSON `copy_mode`
field reports the chosen copy backend, not evidence that any particular stage ran.

A valid cache hit verifies the cached bytes but does not reread object packs.
Consequently it can work despite a missing/corrupt pack; it is not an object-store
scrub. Export, portable restore and `--no-cache` independently require and verify
object contents. The cache is a local optimization, not part of the portable
bundle, and PostgreSQL continues to read and write ordinary branch files.

## Accounting and reproducibility

`forklift stats` reports apparent file bytes, file counts and `st_blocks * 512`
under the complete store, generations, objects, and cache. It includes old/failed
materializations, cached files, quarantines, manifests, orphan packs and temporary files. It excludes
external exports and directory/inode overhead. Allocated counts are explicitly
**not unique physical APFS space**; shared extents can be counted repeatedly.
No memory, unique-sharing, or peak-space claim follows from these counters.

Checkpoint JSON events report scanned bytes, new compressed pack bytes/count,
reused existing bytes/references, duplicates within capture, hash time,
compression time, write/sync time and reuse-validation time. These timers overlap
in interpretation: reuse validation includes decoding and checksum work that is
not counted as new-content hashing. Total scan/publication time includes other
work as well. Export and restore have separate end-to-end stages. See the
[measured milestone report](history/milestone-three.md) for observations and limits.
