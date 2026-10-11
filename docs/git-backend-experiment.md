# Archived integrated Git backend comparison

Forklift retains its original chunked PostgreSQL storage implementation. The Git
backend was implemented and tested as an experiment, then kept out of shipped
runtime code. Native Supabase filesystem snapshots are unchanged.

The [integrated report](../results/2026-10-11-integrated-backends/report.md) records
capture, first/warm restore, independent provisioning, storage and maintenance at
8, 64 and 256 MiB. Capture was faster with chunked storage in every tested class.
Restore timings were close and packed history sizes were mixed. These results
apply to the tested implementations, compressible fixtures and one APFS host.
They do not establish inherent limits of Git.

The earlier [external workflow comparison](../results/2026-10-10-git-comparison/report.md)
uses competent standalone Git/Git LFS lifecycle scripts. It performs different
verification/publication work and is not equivalent to the integrated comparison.
Its [harness](git-benchmark.md) runs against the shipped chunked implementation.

## Reproduce the integrated experiment

The exact experiment remains at commit
`e13ac8dcfb4d054d1e8b3fc6afdc2103e4e442f6`, retained on the local
`experiments/git-storage` branch. The primary measured implementation was
`e3a6e3283968cf7e26dac76c1183bef2913e5c88`; the final commit adds receipts and
the post-packing supplement without changing Rust implementation code.

For a self-contained reproduction without those local Git objects, this branch
also carries [the implementation and test patch](../experiments/git-storage.patch).
Apply it only in a fresh disposable checkout based on this evidence branch.
It includes the Git implementation and focused tests, including the descendant-lock
fix. The benchmark scripts and recorded evidence are already present.

From this repository, choose an unused worktree path:

```sh
git worktree add --detach /tmp/forklift-git-experiment HEAD
cd /tmp/forklift-git-experiment
git apply --check experiments/git-storage.patch
git apply experiments/git-storage.patch
cargo build --locked --release --bin forklift
python3 scripts/benchmark_backends.py --root benchmark-data/integrated-reproduction --output results/local-integrated-reproduction --sizes-mib 8,64,256 --samples 3
python3 scripts/summarize_backends.py results/local-integrated-reproduction
python3 scripts/benchmark_packed_restore.py --fixtures benchmark-data/integrated-reproduction --primary-results results/local-integrated-reproduction --root benchmark-data/packed-reproduction --output results/local-packed-reproduction
python3 scripts/summarize_packed_restore.py results/local-packed-reproduction
```

The integrated scripts require the experimental binary. The shipped CLI does not
accept `config --storage-backend git`. Do not apply the patch to an existing store
or merge it into the shipped implementation merely to reproduce the results.
Use fresh owned clusters and keep PostgreSQL durability enabled. Run serially and
stop owned servers before deleting fixture directories. Read the
[archived design and recovery contract](git-storage.md) before using Git storage.

Raw primary/supplement measurements, validation receipts, regression logs and
excluded pilots are retained beside their reports. The Git backend never uses
application commits or remotes to store database state. No database uploads were
performed. Host power-loss equivalence was not tested.
