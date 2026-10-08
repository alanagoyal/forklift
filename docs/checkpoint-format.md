# checkpoint object format, version 1

Implemented in milestone three. This is a local object repository: no cloud
service, network connection, or new PostgreSQL storage engine is involved.
Running clusters still use ordinary files. Objects store saved, stopped state.

## Layout and identities

```text
STORE/
  catalog.json                    # catalog version 2
  generations/                    # running/stopped/old/failed cluster files
  objects/
    manifests/<sha256>.json
    packs/<sha256>.pack
```

The catalog selects a checkpoint by its manifest's SHA-256 digest. The digest is
computed over the exact UTF-8 JSON bytes. The manifest contains:

- Format version, 1 MiB chunk size and 4 MiB maximum uncompressed pack size.
- PostgreSQL version, major version, platform/architecture and build configuration.
- The installed supported extensions, with database, name and version.
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
accounting. There is no garbage collector in this milestone.

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

Only built-in `plpgsql` version 1.0 is supported. Metadata covers connectable
databases; untouched template0 is assumed to be the standard initdb template.
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

Catalog version 1 is atomically upgraded to version 2 on first use. Legacy
physical checkpoints remain available for branch/reset. The old milestone-two
binary rejects the upgraded catalog; downgrading it manually is unsupported.
A legacy checkpoint can be converted by branching from it and taking a new
checkpoint. Export returns `legacy_checkpoint` with those instructions until then.

New checkpoints are object-backed. Branch and reset from them materialize and
verify files; they do **not** claim APFS clone speed or shared live-cluster blocks.
The `--copy-mode` option still controls legacy physical checkpoint copies only.
The JSON `copy_mode` field reflects that selected legacy option; actual operation
stages and checkpoint metadata identify object capture/materialization. A verified
materialized cache could restore native clone speed later, but is not implemented.

## Accounting and reproducibility

`forklift stats` reports apparent file bytes, file counts and `st_blocks * 512`
under the complete store, generations, and objects. It includes old/failed
materializations, manifests, orphan packs and temporary object files. It excludes
external exports and directory/inode overhead. Allocated counts are explicitly
**not unique physical APFS space**; shared extents can be counted repeatedly.
No memory, unique-sharing, or peak-space claim follows from these counters.

Checkpoint JSON events report scanned bytes, new compressed pack bytes/count,
reused existing bytes/references, duplicates within capture, hash time,
compression time, write/sync time and reuse-validation time. These timers overlap
in interpretation: reuse validation includes decoding and checksum work that is
not counted as new-content hashing. Total scan/publication time includes other
work as well. Export and restore have separate end-to-end stages. See the
[measured milestone report](milestone-three.md) for observations and limits.
