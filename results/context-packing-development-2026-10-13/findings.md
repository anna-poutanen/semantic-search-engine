# Context packing development replay

The prospective plan and both controls were committed before any replay output. The final pre-output commit is `ead12e0`; the initial `b35d4ea` plan was superseded only to close an offline tokenizer-cache guard gap found by independent review. No outcome informed that repair.

All **90 first-repetition baselines** matched exact compact JSON, full rows and source characters before either control was computed. Both controls reused immutable candidate orders and pinned source/static-reference/context code. No model or retrieval calls were made.

A separate verifier checked **270 responses**, **2055 exact saved-source excerpts** and **113 pinned source files** without production helpers or gold labels. All responses satisfy the eight-excerpt, 12,000-character and 4,096 exact compact-token caps.

| Repository / condition | Baseline rows | Backfill rows | Containment + backfill rows | Control cases changed / 15 | Covered-prefix skips |
|---|---:|---:|---:|---:|---:|
| express / bm25 | 120 | 120 | 120 | 2 | 3 |
| express / dense | 120 | 120 | 120 | 4 | 8 |
| express / hybrid | 120 | 120 | 120 | 4 | 7 |
| zod / bm25 | 104 | 107 | 105 | 4 | 6 |
| zod / dense | 110 | 110 | 112 | 7 | 11 |
| zod / hybrid | 108 | 110 | 109 | 7 | 12 |

Containment changed **28/90** control responses and skipped **47** wholly covered prefixes after clipping. The table includes present and absent authored queries. Row counts measure delivery, not relevance. Some streams deliver fewer rows because later whole lines do not fit; backfill does not guarantee eight usable excerpts.

The selector receives no target paths, evidence lines, query kinds or ranks. This report also uses no gold labels. Evidence improvements, losses and multi-file coverage must be scored separately against the retained labels before interpreting relevance. Containment only considers earlier delivered intervals; later larger windows may still overlap or contain earlier smaller excerpts.

Both repositories and Zod outcomes had already been observed. This is a development counterfactual on frozen candidate orders, not a new holdout, production/default change, retrieval rerun, speed measurement, provider-token/billing result or coding-correctness study. The existing miss categories do not prove that packing caused those misses.

Plan SHA256: `508d68e631e12098095240636956380060029e20d4a12a5b18b640b76b78acf1`. Replay SHA256: `23993af056c5e1c35cf055b407cf6f188d81ac682d573f7ece2fd295a4dad88e`.

See [the prospective protocol](../../docs/context-packing-replay.md), [plan](plan.json), [raw replay](replay.jsonl), [run summary](summary.json) and [independent CPU audit](cpu-audit.json).
