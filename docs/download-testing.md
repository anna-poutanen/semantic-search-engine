# Daily release download testing

The [GitHub Actions workflow](../.github/workflows/download-tests.yml) runs
`scripts/test_release_downloads.py` daily at 9:00 a.m. America/Toronto, including
daylight-saving changes. It uses a GitHub-hosted Ubuntu runner and does not need
your Mac or Codex to be running. Each run chooses an integer uniformly from 2 through
15 and makes that many sequential download attempts against the newest published
Mac ZIP, including preview releases. It queries public GitHub release metadata
to discover the asset, so it does not depend on a hardcoded release tag.

Each attempt checks the downloaded size, GitHub's SHA-256 digest, ZIP CRCs and
required launcher/manifest/license files. Downloads stay in memory and are not
installed or executed. No model weights are downloaded. The request user agent
identifies the traffic as synthetic download testing. These requests may be
included in GitHub asset download counts; they do not represent user adoption.

Open [Daily release download tests](https://github.com/anna-poutanen/semantic-search-engine/actions/workflows/download-tests.yml)
to see runs, summaries and downloadable JSON report artifacts, retained for 90
days. GitHub may delay scheduled jobs; public-repository schedules can be
disabled after 60 days without repository activity. See
[GitHub's schedule documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).

Runs are serialized. Before making downloads, the workflow checks for today's
`release-download-claim-YYYY-MM-DD` artifact and uploads that marker if absent.
Repeated manual runs and reruns skip downloads once the day is claimed, including
after a failed or interrupted run. Keep these marker artifacts to preserve this
guard. Failed attempts are recorded without retries. An
interrupted run is retained for inspection and is not repeated that day, so an
outage or interruption can produce fewer successful downloads than planned.

Use **Run workflow** on the Actions page to trigger the same guarded checks
manually. The optional count input accepts 2–15; the default is random. A skipped
run does not imply the original test succeeded: inspect the original report.
The previous local Codex schedule is paused to avoid duplicate testing.

For local development only:

```sh
.venv/bin/python scripts/test_release_downloads.py
```

Local reports live in ignored `results/local/download-tests/YYYY-MM-DD.json`,
using Toronto calendar dates. A local daily lock and report prevent repeated
local downloads, but do not share GitHub's marker: use Actions for manual checks
to preserve the cloud daily cap. `--count 2` through `--count 15` overrides the
local random selection. Existing daily reports are returned unchanged. A failed
or incomplete report exits nonzero. Failure notifications follow your GitHub
Actions notification preferences.
