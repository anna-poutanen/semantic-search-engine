# Observed native provider comparison — 2026-11-10

The exact float32 validator reduces real provider cost, but whole-search
improvement is modest and still fails the fixed latency ceiling. The quality
rule is unchanged: its known hybrid omission remains. Keep production and
release 0.1.5 on coverage/backfill. The [independent audit](independent-audit.json)
passes all 336 searches, eight warmups and 84 exact original/optimized role pairs.
Technical integrity and semantic parity pass; latency and hybrid quality
qualification fail.

## Actual resident search latency

All 14 already observed attrs queries ran with four conditions, dense and
hybrid retrieval, and three rotated repetitions: 336 measured searches, plus
eight fixed generic warmups. Each cell below uses all 42 measured searches for
that arm/mode. Times cover the complete resident `backend.search`, including
factory/clone setup, actual source/SQLite/model metadata checks, original query
and fresh clause inference, validation, selection and postprocessing. Archive
serialization and diagnostic reporting follow the timer.

| Condition | Dense median | Dense P95 | Hybrid median | Hybrid P95 |
| --- | ---: | ---: | ---: | ---: |
| Legacy | 283.2 ms | 333.7 ms | 280.5 ms | 328.1 ms |
| Coverage control | 276.0 ms | 345.0 ms | 284.3 ms | 342.6 ms |
| Original role provider | 691.5 ms | 805.2 ms | 717.9 ms | 839.4 ms |
| Exact float32 provider | 659.6 ms | 782.8 ms | 663.3 ms | 742.3 ms |

The optimized/original median ratios are **0.954 dense and 0.924 hybrid**:
4.6% and 7.6% lower medians in this series. Median paired fast-minus-original
deltas are −26.5 and −57.1 ms. These are descriptive observations from one
series, not population estimates or confidence bounds.
Some individual pairs are slower: P95 paired fast/original ratios are 1.103
dense and 1.115 hybrid. Lower overall medians do not imply every request improves.

Optimized search still takes **2.39 times coverage in dense and 2.33 times in
hybrid**, versus the prospectively fixed maximum of **1.25 times each control**.
Its ratios to legacy are 2.33 and 2.36. Both modes fail that ceiling; hybrid
cannot rescue the primary dense gate.

| Nested diagnostic | Dense original → optimized | Hybrid original → optimized |
| --- | ---: | ---: |
| Provider-call median | 359.7 → 281.6 ms | 363.6 → 286.4 ms |
| Factory/clone median | 0.018 → 0.060 ms | 0.018 → 0.057 ms |

The real provider saves about 78 ms at its median, consistent with a useful
validation optimization. The earlier saved-vector CPU medians of 82.2→15.8 and
84.3→18.2 ms excluded real inference, source scans, SQLite and metadata work.
Whole searches still execute that work, so a roughly fivefold reduction in one
saved CPU component does not imply a fivefold search speedup. Nested medians
and paired whole-search medians are not additive; this experiment does not
causally assign their remaining difference to a particular stage. There are no
repeat-query feature-cache hits or skipped clause inferences in either role arm.

## Quality and executing work

First-repetition quality uses all 12 present queries, including eight multi-file
and four single-file tasks, plus two absent controls. Original and optimized
summary counts match:

| Mode | Coverage complete / multi-file / groups | Role complete / multi-file / groups |
| --- | --- | --- |
| Dense | 6/12 · 2/8 · 24/35 | 7/12 · 3/8 · 25/35 |
| Hybrid | 7/12 · 3/8 · 24/35 | 7/12 · 3/8 · 25/35 |

Dense retains the earlier validation-context gain without an individual group
loss against either control. Hybrid still loses
`attrs_optional_context_converter_and_generated_arguments`, group 4, against
coverage, while gaining validation-context evidence. Its unchanged aggregate
therefore does not mean preservation. The separate annotated-dependency rule
still fails its prior quality gate and is not enabled here. This phase adds no
new complete behavior and evaluates no fresh codebase.

The complete run records 336 original-query embeddings, 156 fresh clause-query
API calls encoding 456 clause texts, and zero measured document-embedding calls.
A new owned SQLite index embeds all 204 source windows during setup. Query
vectors, raw rankings and compact outputs remain unchanged across repetitions.
The independent auditor verifies exact original/optimized features, receipts,
result metadata and compact bytes for all 84 pairs. Only explicitly named clock
values differ, after separate validation of their enclosing bounds and actual
work. It makes zero model, index, retrieval or network calls and imports no
producer, provider, backend, model or NumPy runtime. It reconstructs 492 measured
model forwards (336 original-query and 156 clause forwards), separately from
51 setup document forwards and eight query warmups. It uses the disclosed frozen
independent helper/graph trust boundary in the protocol.

## Frozen inputs, failures and validation

The [protocol](../../docs/provider-native-protocol.md) freezes the conditions,
complete source/input/parent closure, timing boundaries, typed parity policy,
individual quality checks and 1.25 ceiling before model execution. The dataset
is attrs revision `3dca08ce5bbe673d7df25f44f3dda92505d1043d`, with 19 supported
source files, 204 windows, 202 definitions and the original graph. It reuses all
original labels and source-inventory bytes; attrs has already been observed.

- Original source freeze: `dab55c239cf3a451ec7c389f0133c84ad7917e22`.
- Source-only wrong-path and default-window preparation failures are preserved
  in `1d183db` and `e9b71a9`; neither created a plan or loaded a model. The
  corrected checker explicitly matches the historical 160/40 windows.
- The [first native attempt](../provider-native-observed-2026-11-10/attempt/failure.json)
  indexed 204 windows, completed one generic legacy/dense warmup query, then
  failed archive validation before measured searches. Commit `7da19bc` preserves
  its partial setup and four finalized empty gzip ledgers, model closure and
  lease release. Its raw warmup response was not saved; no response is
  reconstructed and no latency result is claimed. Independent read-only review
  verified all 12 preserved files against that commit and both hash manifests.
- An invented CPU reproduction identifies tuple-valued native origins/definition
  keys as the archive incompatibility. The new boundary maps tuples to JSON
  arrays exactly like the original native writer, preserves dictionary order,
  retains strict finite literal validation and durable writes, and preserves
  explicit safe row snapshots on subsequent archive errors. The regression
  compares all eight real invented arm/mode traces byte for byte with the
  historical JSON transport.
- Corrected source freeze: `afa79850d0463d7ec306c21848d0843cdf07267d`.
  All **161 experiment tests** pass, including complete real-backend invented
  trace reconstruction and archive/failure boundaries. Ruff, whitespace and
  independent source reviews pass; original production/research algorithms are
  unchanged. [Full source CI](../provider-native-preflight-2026-11-10/transport-source-ci.json)
  passes **5,574 tests, 10 skipped**, plus the editor checks.
- [V2 source-only input audit](input-audit.json) passes with 220 source blobs,
  500 immutable parent artifacts, full prior failure/quality evidence and all
  eight model/index/retrieval/selector/scoring/outcome work counters zero.
  Input freeze and native execution:
  `0f76a97a83981734250cbd4f250f1a7d3b020f31`.
- [Complete producer summary](attempt/summary.json), all three 112-request
  archives, eight-warmup ledger and closure receipts are preserved in commit
  `23e2f7f`. No slow sample was discarded and no measured attempt was resumed.
- The frozen full independent audit passes at `23e2f7f`; receipt SHA-256
  `f16b369f7e132b57fab3e4f16b4ca2ad0c7f4fc072d45c859908dd25b004e8ac`.
  It checks all source/input/parent and native artifact bytes, actual forward and
  resident-vector bindings, baseline/proposal/guard reconstruction, exact parity,
  individual quality operands and whole-search summaries. No diagnostic wording
  normalization was needed. Its [execution log](../provider-native-preflight-2026-11-10/independent-audit-v2.log)
  is preserved alongside the source/test/CI receipts.

The machine is macOS 27.0.1 arm64, Python 3.12.15, the pinned unquantized
Qwen3-Embedding-0.6B checkpoint/runtime and exact offline cl100k tokenizer. Power
receipts show battery at 63% after setup and 62% after execution; this is not a
controlled mains-power experiment. An owned idle-sleep inhibitor runs for the
native command only. No concurrent local heavy tests run during measurement.
Three archive shards remain below GitHub's individual-file limit without
omitting native vectors or duplicate evidence.

## Next decision

Keep the optimization experimental. A separate, prospectively frozen quality
experiment should first test a small literal returned-template role against
all observed individual-loss checks, including ordinary message formatters as
adversarial cases. This may protect already delivered hybrid evidence; it
cannot admit the dense formatter that coverage omitted. Dense admission needs
its own budget-preserving selector experiment. The
[source-only design notes](../provider-native-preflight-2026-11-10/quality-design-notes.md)
separate those hypotheses and their limitations. Require a distinct new
multi-file gain, no individual loss, native latency qualification and then a
newly untouched codebase before revisiting coding correctness/time/token trials.
No production/default/release change or coding-token savings follows from this
performance result.
