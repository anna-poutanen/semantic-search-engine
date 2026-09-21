# Independent static batch scorer review

**Passed static review**, bound to batch module SHA-256
`53247c96c416b655d714859691bc38f08af4aaccabfe01770acf86ed4c4221fd`.
The [receipt](batch-static-review.json) records inspected code/native API hashes;
the [reviewed source snapshot](batch-reviewed-source.py.txt) preserves that version.
No model weights or native model runtime were loaded, and no inference ran.

Installed MLX-LM Qwen3 creates native causal attention with equal query/key
sequence lengths and independent batch examples. The pinned checkpoint uses
default RoPE with offset zero. Right padding therefore cannot affect the retained
real-token prefix: its keys are future positions, while the selected state is
each row's last real token. Normalization and MLPs remain per token. The head
projects only that one state per row. Every singleton delegates the unchanged
original scorer; larger batches restore original candidate order after stable
length scheduling.

The schedule retains every pair, allows sizes 1/2/4/8, caps each sequence at
8,192 tokens and each actual padded forward at 16,384 input slots. Started and
completed/failed receipts bind original indexes and exact input/source hashes;
batch-forward and pair counters remain separate. The original model/checkpoint,
instruction, tokenizer and literal query/source formatting are preserved.

**51 injected tests passed**; isolated import-free runtime preflight found all
three pinned packages and inspected source hashes with zero errors. These fake
tests do not certify native BF16 numerical parity. The predeclared resident parity
gate must pass before actual comparative scoring/packing and latency measurements.

Three nonblocking notes were sent to the author/root before inference. The first
two are now clarified in the final source/note; cleanup remains a resident
integration obligation:

- The input-slot cap is not a GPU-byte or OS-RSS limit; measure actual allocator
  peaks for the tested configurations.
- Native `swiglu` uses `mx.compile(shapeless=True)`. Restrict the no-cache wording
  to input/KV/result caching; compiled kernel reuse is part of a warm runtime.
- The engine scorer closure retains the model. Worker shutdown must release both
  engine and returned ownership references before clearing allocator cache, and
  retain a cleanup receipt.

No blocking source defect was found. This review does not establish retrieval
quality, warm latency, model numeric parity or end-to-end worker cleanup.

The initial review and reconstructed hash-verified source snapshot are retained
as `batch-static-review-initial.json` and `batch-reviewed-source-initial.py.txt`.
Reversing only the revised docstring reproduces the initial source hash exactly;
no executable scoring mechanics changed. Final tests and preflight passed again.
