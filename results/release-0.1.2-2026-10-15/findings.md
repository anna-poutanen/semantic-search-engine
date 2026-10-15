# Regular v0.1.2 release promotion

The regular release promotes the application already qualified in
`v0.1.2-preview.1`. Current download documentation now points to `v0.1.2`; the
README version badge follows regular releases, and its requested download-count
caption was removed. Historical preview evidence is unchanged.

The small ZIP was rebuilt for its updated guide. Comparing all 61 archive entries
found exactly two changed files: `README.md` and `BUNDLE_MANIFEST.json`. All runtime
files, dependencies, notices and the embedded VSIX remain byte-identical to the
qualified preview. The application source digest is unchanged, and all 35 bundle
tests passed. No new model run was necessary for this documentation and release
metadata change. See [the comparison](promotion.json) and
[existing runtime qualification](../beta-hardening-2026-10-14/findings.md).
