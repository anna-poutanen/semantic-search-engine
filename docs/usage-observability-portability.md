# Future diagnostic portability

The usage diagnostic now creates its owned empty fixture beneath
`Path('/tmp').resolve()`. That canonical path is `/private/tmp` on the tested Mac
and `/tmp` on Linux. Creation, the filesystem gate and cleanup tests use the same
constant. This avoids assuming that Linux has a `/private/tmp` directory and
continues to check that the fixture is outside home, the project and every Git
ancestor before a native process can start. Neighboring directories and retained
artifacts are preserved.

This is a change for future executions. The historical Mac diagnostic's executed
source remains available at Git revision `03a02da`; its original protocol,
prospective plan, recorded script hash and actual results are unchanged. No
diagnostic, native app-server process or model call was rerun for this repair.
The existing protocol's `/private/tmp` wording describes that original Mac run.

CPU tests include real creation/removal on the host's canonical `/tmp`, a
Linux-shaped external-parent override, startup refusal, nonempty/Git gates and
preservation of neighboring data. The override exercises the path policy on the
test host; it is not a claim that a Linux native diagnostic ran. The local dense
model runtime still targets Apple Silicon macOS.
