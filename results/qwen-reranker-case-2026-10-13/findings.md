# Observation wrapper failed before scoring

The checkpoint was verified and loaded, but the observer accepted five positional arguments while the reviewed scorer supplied six. The first call failed before entering the observer, so zero pairs were scored and no ranking outcome exists. The model cleanup ran through the reviewed finally block; the owned process group stopped. The original plan, input, source, empty forward log and failure/supervisor receipts remain unchanged.

Root corrected only the logging signature and froze the same model, query, 50 inputs and ranking policy in a separate v2 directory. No semantic outcome informed the correction.
