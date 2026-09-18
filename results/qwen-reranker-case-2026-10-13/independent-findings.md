# Preserved reranker instrumentation failure

The wrapper signature rejected the six-argument scorer call after model weights loaded and parameters were evaluated. Zero pair forwards completed, so there are no ranking or quality outcomes. The original failure/source/inputs remain preserved.

Weights were loaded and parameters evaluated before Python rejected the wrapper's six-argument call. This is not a zero-model-load gate failure; no pair backbone or score was executed. Cleanup additionally relies on frozen prototype finally and owned child termination.
