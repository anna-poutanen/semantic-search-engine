# Independent clause-fusion development audit

Both Express and Zod were already observed. This is one frozen pass over 30 authored development queries, two conditions each; it is not a new holdout or a default change.

All 60 conditions, 4300 raw ranked windows and 460 delivered excerpts pass independent reconstruction/source/budget checks. The original and fused conditions use fresh query embeddings; document-vector setup is shared per repository and reported separately.

| Repository / condition | Candidate complete | Delivered complete | AND complete | Delivered groups | Absent with decoys | Query API calls | Median / P95 in-process seconds |
|---|---:|---:|---:|---:|---:|---:|---:|
| express / full_query_dense | 12/12 | 10/12 | 2/4 | 17/19 | 3/3 | 15 | 0.108 / 0.117 |
| express / clause_rrf | 12/12 | 10/12 | 2/4 | 17/19 | 3/3 | 31 | 0.147 / 0.161 |
| zod / full_query_dense | 11/12 | 8/12 | 0/4 | 13/19 | 3/3 | 15 | 0.196 / 0.355 |
| zod / clause_rrf | 10/12 | 8/12 | 0/4 | 13/19 | 3/3 | 25 | 0.221 / 0.307 |

Across all 24 positive pairs: 0 complete gains and 0 complete regressions; among eight AND queries, 0 gains and 0 regressions. Absent queries remain separate and plausible candidates do not prove behavior or absence.

Raw full-query cosine remains separate from RRF priority. Every delivered payload retains the shared eight-result, 12,000-character and exact 4,096-token caps. Extra query API calls and in-process intervals are descriptive costs; they are not provider billing or HTTP latency. All paired changes, missed groups and denominators are in the JSON.

Candidate-completeness changes: 0 gains and 1 regressions. The latter are retained even when delivered coverage was already incomplete. Two cold document API calls embedded 1983 unique document texts for 1992 windows; setup took Express 6.368s and Zod 107.892s. These are API calls, not counted Metal forwards.

Limitations:

- Both repositories and all queries were already observed; this one-pass development probe is not a new holdout, generalization test, coding-correctness test or justification for changing defaults.
- All OR alternatives and required AND groups are rescored from complete executable spans in one exact saved-source excerpt per group. Plausible absent-query decoys receive no positive gold credit.
- Independent RRF and exact compact payload checks use retained ranks and source bytes. Retained complete query vectors reproduce their byte SHA/norm; the auditor does not rerun semantic embedding or dense retrieval.
- Observed attempted/completed embedding API calls and actual input logs are checked, not individual internal MLX batch forwards or provider usage.
- Condition intervals are in-process embedding/retrieval/fusion/packing observations; prepared-vector backend timings are not model latency. Cold index setup is separate; memory is process-lifetime and descriptive.
- Source files, package metadata, checkpoint metadata and closed index byte hashes are checked. Retained complete weight-file SHA/size verification matches pinned provenance; the auditor does not reread or load weights.
- No model, retrieval, HTTP, hosted service or native app is executed by this auditor. The prior independent source-only scoring helper is reused; no producer or production ranking helper is imported.
