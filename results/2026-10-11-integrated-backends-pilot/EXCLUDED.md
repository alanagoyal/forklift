# Pre-lock pilot, excluded from the final comparison

This complete 8/64/256 MiB run passed its serial correctness checks, but measured
binary `1df1159111f4d968b9c7eaf20cd9ed5b4170b683fb7a420d6495b7093656d247`
before the repository lock was inherited by Git descendants. Review identified
that an orphan Git GC could otherwise race a later Forklift writer after parent
process death. The final implementation fixes and tests that case. These receipts
are retained for provenance, not pooled with the corrected binary's measurements.

The output directory was renamed after completion; recorded argv names its original
location. All servers were stopped. No compilation or testing ran concurrently
with these measurements; only review/source edits for the fix occurred near the end.
