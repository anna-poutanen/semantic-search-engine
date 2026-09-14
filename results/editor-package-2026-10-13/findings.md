# Installed preview package passes

The frozen VSIX contains 21 files and is 51,493 bytes, SHA-256
`be4f32bdb58e45693244a933dbfd2effc35fdaaa4cc3f3bb44237bb1c3b9da9b`.
It installs into an owned temporary VS Code profile. The actual Development Host
activates that installed payload, rather than the repository's extension source.
Its test entry remains separate repository code.

The successful [host receipt](packaged-host.json) has seven searches and 35 exact
excerpts, including four literal dirty-buffer excerpts. Preview, navigation,
all-input freshness, saved-only fallback, status and cancellation pass. Saved
fixture bytes remain unchanged. Installed code and the frozen VSIX are unchanged
before and after the host; temporary profiles are removed and owned process
groups stop. The external Qwen backend was stopped after its runtime capture.

The [independent audit](packaged-host-audit.json) verifies every archive member,
all 20 installed files before/after, normalized manifest equality, request and
redacted-response hashes, exact physical source/buffer bindings and compact caps.
It also checks 57 source hashes in the [runtime companion](runtime-proof.json).
Original HTTP byte hashes and raw installer metadata hashes cannot be recovered
from their parsed/redacted public counterparts; that limitation is explicit.

The [first actual attempt](packaged-host-attempt01-payload-membership.json)
installed successfully but failed strict membership before any host/search.
Its original source and independent failure audit are retained. The first
installed inventory was not retained. The pinned Microsoft extraction code maps
`extension.vsixmanifest` to `.vsixmanifest`; the revised gate admits only that
metadata name with bytes exactly matching the frozen ZIP. A separate successful
attempt confirms this mapping and retains full inventories. Arbitrary extra or
changed code still fails. See the [packaged-host recipe](../../docs/packaged-editor-host.md).

This establishes packaged integration in a trusted authored Development Host.
Ordinary user-profile activation, actual Restricted Mode/multi-root/offline-service
checks and unseen-workflow benefits remain broader acceptance work. The preview
retains UNLICENSED and makes no Marketplace publication or new license grant.
