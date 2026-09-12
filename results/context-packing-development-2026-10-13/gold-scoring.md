# Development packing replay: independent evidence scores

Both Express and Zod and their earlier outcomes had already been observed. These scores describe a post-selection development replay of fixed candidate orders. They do not create a new holdout or change the shipped packing policy.

All 90 first-repetition baselines match the original responses. The independent scorer checked 270 payloads and their actual compact token caps, 2055 saved-source excerpts and 113 pinned files. Gold credit requires a complete executable evidence span in one verified excerpt; OR alternatives and all required AND groups retain the original language-study rule.

| Repository / retrieval | Complete baseline | Backfill only | Containment + backfill | AND baseline / backfill / containment | Groups baseline / backfill / containment |
|---|---:|---:|---:|---|---|
| express / bm25 | 10/12 | 10/12 | 10/12 | 2/4 / 2/4 / 2/4 | 17/19 / 17/19 / 17/19 |
| express / dense | 10/12 | 10/12 | 10/12 | 2/4 / 2/4 / 2/4 | 17/19 / 17/19 / 17/19 |
| express / hybrid | 11/12 | 11/12 | 11/12 | 3/4 / 3/4 / 3/4 | 18/19 / 18/19 / 18/19 |
| zod / bm25 | 7/12 | 7/12 | 7/12 | 0/4 / 0/4 / 0/4 | 11/19 / 11/19 / 11/19 |
| zod / dense | 8/12 | 8/12 | 8/12 | 0/4 / 0/4 / 0/4 | 13/19 / 13/19 / 13/19 |
| zod / hybrid | 7/12 | 7/12 | 7/12 | 0/4 / 0/4 / 0/4 | 11/19 / 11/19 / 11/19 |

| Paired comparison | Complete gains | Complete regressions | Cases with changed group coverage | Cases with changed evidence ranks |
|---|---:|---:|---:|---:|
| backfill_only versus baseline | 0 | 0 | 0 | 0 |
| containment_and_backfill versus backfill_only | 0 | 0 | 0 | 3 |
| containment_and_backfill versus baseline | 0 | 0 | 0 | 3 |

These comparisons include all 72 positive query/condition cases, including regressions. The 18 absent cases have no executable gold and are reported separately in the JSON; returned candidates do not establish absence or an abstention success. Retrieval conditions reuse the same 30 authored queries, so pooled cases are not independent user trials.

The backfill control uses the same expanded stream and reclaims characters removed by token clipping. Compare containment with that control before attributing differences to containment. Neither replay measures fresh rankings, speed, model behavior, provider tokens/billing or coding correctness. All source caps remain eight excerpts, 12,000 characters and 4,096 exact compact tokens.

Per-query rank transitions, complete gains/losses and all denominators are retained in [gold-scoring.json](gold-scoring.json). Original selector output and no-gold technical findings remain unchanged.
