# Independent precision-decomposition audit

Independent CPU precision-decomposition diagnostics; shared-state observations are not batching approval

All original R1 controls exactly reproduced: 1664 / 1664.

Stored native BF16 vectors, exact float32 hashes/values, complete original token/source inputs, all scalar variants, tracked operations and owned cleanup are independently verified. The JSON includes per-set max/mean/RMS hidden differences and unchanged diagnostic gates for every projection variant.

- Interpret R1 causes only if all original lazy scores reproduce the frozen R1 scalar/input witnesses; mismatch leaves attribution unresolved.
- Per-row scores reuse evaluated batched hidden states. Favorable diagnostic gate results require a new frozen actual-request parity experiment before any corrected batch configuration qualifies.
- Every graph-construction/projection operation is reconciled; auxiliary materialization/float32 serialization may evaluate graphs and is not itself an independently measured backbone forward or reranking request.
- No retrieval, embedding, fresh codebase evaluation, provider tokens, production latency or numerical official/PyTorch parity is established by this diagnostic.
