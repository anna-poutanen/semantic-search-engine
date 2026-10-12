# Experiments

- [Flask / H2O q8, October 12, 2026](flask-h2o-q8-2026-10-12/report.md):
  M4 MacBook Pro, 24 GB RAM, frozen developer-authored dataset. Correct first
  result increased from 8/12 to 11/12 using the same ten BM25 candidates. The
  scorer preferred the correct implementation over two labeled decoys on all
  12 diagnostic queries. Warm search median was 19.3 seconds; peak facade physical
  footprint was about 7.5 GB according to macOS.

H2O is useful for ordering candidates in this pilot, but does not meet the
five-second interactive latency target. The remaining search miss is a candidate
retrieval failure. Embeddings/hybrid retrieval, fewer scored candidates, and
unseen-query evaluation are the next experiments. Paid-agent token savings have
not been measured.

Each committed run contains `results.json`, raw `judgments.jsonl`, and `report.md`.
Local repeats should use `results/local/<new-run>` so development output does not
replace this frozen run.
