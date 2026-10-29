# Complementary context v2: pre-freeze independent review

Reviewed at 2026-10-29T18:59:58.389919+00:00 by the independent source-evaluation reviewer. Research checkout HEAD: `c9a3184ce3b6945c3a3be1d06bef3ef61896a29a`. The new experiment files were uncommitted during review; the prospective plan must bind and commit these exact bytes before any observed candidate outcomes.

No remaining blocker was identified in the reviewed scope after the corrections below. This is a code, protocol and invented-fixture review. It is not a qualification result, fresh-codebase validation or latency claim. Production remains on the unchanged definition-aware coverage policy.

## Findings and corrections

1. **Unresolved candidate graph identity.** A winning window without its own canonical definition key could receive a graph witness from a different known definition sharing its display label. The new producer and independently implemented auditor now require the winning window’s own exact canonical key to equal the unique graph label before adding shared-parent or sibling protection. Mixed known/unknown same-label regressions cover the case. Unknown windows still remain separate window identities; the allocation rule and ranking constants were not tuned.

2. **Complete control-barrier receipt audit.** Checking only barrier counts and plan identity left its individual case receipts unaudited. The independent auditor now reconstructs all 786 source-view, compact-payload and immutable-reference receipts and compares the entire frozen control-parity and actual control-gate documents canonically, including the plan hash and 2,948 historical negative references. Adversarial receipt tests cover corruption.

3. **Literal protected score types.** Python numeric equality accepted a boolean or integer in place of an original protected floating-point scalar. The new source guard and its independent reconstruction now preserve score-field presence, exact value type and value for the same original window. Typed scalar tamper tests cover the correction. A containing different retrieval window retains its own original scalar.

## Reviewed contracts

The review checked the protocol against both implementations for the fixed two-window raw portfolio and dependency exceptions; exact Fraction document frequencies, aspect support, gain and rank-based utility; deterministic ties; first capacity-blocked winner termination with accepted-prefix retention and blocked-trial rollback; original eligible order, scalar and provenance preservation; actual physical-prefix character/token packing and deterministic coalescing/backfill; protected delivered source and delivered-only aspect floors; exact reference fallback; one actual planner/coalesce/guard and zero-or-one lazy factory call with self-reference object identity; exclusive attempts and readable partial ledgers; historical source/blob proofs and prospective source/input bindings; and zero individual group, complete-positive or single-file-first-hit loss against both controls plus a distinct complete multi-file gain over coverage.

The independent auditor uses prior independent packing, source and baseline helpers and an immutable data-only input reader. It does not import the candidate producer, backend, embedding implementation or numerical runtime. Existing negative evidence is referenced rather than rerun or relabeled. The observed matrix remains 393 cases, 201 distinct dataset/query units and 786 controls; reused modes and study rows do not create new independent query units.

## Validation and limits

- Final reviewer-run focused suite: **125 passed in 18.71 seconds**, across the five source-rule, independent-audit, producer/replay, actual toy-backend and immutable-input test files listed below. These include invented 393-row/201-unit producer-to-independent-auditor roundtrips, packing and source/scalar tampering, both-control quality-gate checks, call counts, cancellation/source-snapshot contracts, and interrupted streaming.
- Earlier project-wide suite, reported by the parent agent before the review corrections: **3,667 passed, 10 skipped in 16m35s**. That earlier run is separate from the final focused check and is not represented as a full-suite run after the corrections.
- Parent preparation reported exact parity for all 786 controls and zero observed candidate outcomes. The reviewer did not rerun the recorded observed matrix.
- The reviewer read no reserved attrs corpus, source or gold and invoked no observed candidate outcomes or real model inference. Test execution used invented fixtures and toy backend/index operations with fake embedders; it made no real-corpus indexing/retrieval or new study-data retrieval calls.
- Outcome quality, generalization, warm native latency, coding correctness and provider-token savings remain unmeasured for this new rule. A passing development replay still needs the separately frozen fresh/native validation described by the protocol.

## Reviewed byte identities

These SHA-256 values identify the final reviewed source, protocol and five test files. The preparation review itself should be separately included in the prospective input bindings.

| File | SHA-256 |
| --- | --- |
| `scripts/complementary_inputs.py` | `4df776a142d6b89ba570041f52128b4e230df9427f73a0ca359b05f6abcc0992` |
| `scripts/audit_complementary_context.py` | `15487475fea8924cdf614da4161468416e1d7046a058ac3e7d373826624f1cf3` |
| `scripts/replay_complementary_context.py` | `d2341ed5fef598cca95bc23bfb2354aaa3abd19dce4fe8c1db86759f5708d988` |
| `src/semantic_search/complementary_context.py` | `e7ae6f3e2138c2adb3786ee1e72f8be0b50b092a20806a477c75ebde94b1cc32` |
| `docs/complementary-context-v2-protocol.md` | `006a190030160c5e0e3cb422c7b503426a397280d102117aaf22a0512d7b916e` |
| `tests/test_complementary_inputs.py` | `fb3ac1e8d3f941f1e07e284b3b7eed3ea841ff8e2db41532022ff3b7ae57006f` |
| `tests/test_complementary_context_audit.py` | `13f1eed87343f9ed40159f2821a6bbdb081eb57cac6adeaeb8b60e498dfb5410` |
| `tests/test_complementary_context_replay.py` | `a011c09f471ebb4c7782a1a4bb4ab11552a670c722776e32befd9645624fac8e` |
| `tests/test_complementary_context.py` | `4279f6b7ad03cbf04b68e76dde480d15a5f42f49bdbb746c9a67ab3acd9df875` |
| `tests/test_complementary_context_backend.py` | `7a1d0504691ea7711e71e44039952407ca003df178bbd620f238c382e381e9ad` |

Final focused command:

```text
env PYTHONPATH=src /Users/annapoutanen/personal-projects/semantic-search-engine/.venv/bin/python -m pytest tests/test_complementary_context.py tests/test_complementary_context_audit.py tests/test_complementary_context_replay.py tests/test_complementary_context_backend.py tests/test_complementary_inputs.py -q
```
