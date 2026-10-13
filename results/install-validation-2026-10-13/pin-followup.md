# Protobuf lock closure follow-up

Added **protobuf==7.36.2** to the Mac runtime lock, preserving every existing
pin. This was the version already installed by the initial fresh resolver;
the installed environment did not change. The dependency chain and wheel
metadata are in [dependency-metadata.json](dependency-metadata.json).

The follow-up **reused** that validated environment. Setup passed in 0.537
seconds, `uv pip check` passed, and 118 CPU checks passed in 12.89 seconds
(13.06-second observed test-process interval). All 73 runtime/parser packages
now match lock entries. The target also contains the editable project and
pytest/iniconfig/pluggy test tools, classified separately. No additional runtime
distribution is unlisted. Build-isolation tools are outside the target
environment; their exact versions were not recorded. `setuptools>=75` and
unhashed wheels remain reproducibility limits.

[pin-followup.json](pin-followup.json) records the old/new lock SHA-256, unchanged
installed versions, normalized distribution closure, commands, timings, logs,
and unchanged main-project package inventory. No model directory was created;
no weights were downloaded and no model inference ran. The initial
[results](results.json), [corrected initial CPU audit](followup.json), and
byte-identical [initial lock](requirements-mac-initial.lock) remain available.

One earlier reuse attempt completed setup, dependency checking, and CPU tests,
then its reporting collector failed while combining a dictionary with a set.
Its [error record](reuse-attempt01-collector-error.json) and stage logs are
retained. The collector was corrected and the same environment was rechecked;
this does not constitute another fresh or cold installation.
