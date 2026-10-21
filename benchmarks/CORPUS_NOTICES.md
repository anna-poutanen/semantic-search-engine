# Additional benchmark corpus notices

These ignored checkouts are fetched separately. Published search results, index exports, and raw model judgments can contain their source excerpts; those excerpts retain the following upstream licenses and copyright notices. The accompanying license files are byte-for-byte copies from the pinned revisions. No separate upstream `NOTICE` file was present in these five releases.

| Corpus | Release and pinned commit | License | Upstream copyright notice |
|---|---|---|---|
| [Pallets Click](https://github.com/pallets/click) | 8.1.8 — `934813e4d421071a1b3db3973c02fe2721359a6e` | BSD-3-Clause, [CLICK_LICENSE.txt](CLICK_LICENSE.txt) | Copyright 2014 Pallets |
| [Starlette](https://github.com/encode/starlette) | 0.45.3 — `4d72fd87ea1c358691a19468168f7217d8ca6a87` | BSD-3-Clause, [STARLETTE_LICENSE.txt](STARLETTE_LICENSE.txt) | Copyright © 2018, [Encode OSS Ltd](https://www.encode.io/). All rights reserved. |
| [Rich](https://github.com/Textualize/rich) | 13.9.4 — `43d3b04725ab9731727fb1126e35980c62f32377` | MIT, [RICH_LICENSE.txt](RICH_LICENSE.txt) | Copyright (c) 2020 Will McGugan |
| [Express](https://github.com/expressjs/express) | v5.3.0 — `cf6722bf89a86905281d9160b6482e08166b5ef0` | MIT, [EXPRESS_LICENSE.txt](EXPRESS_LICENSE.txt) | Copyright (c) 2009–2014 TJ Holowaychuk; 2013–2014 Roman Shtylman; 2014–2015 Douglas Christopher Wilson; full original notices in the license copy |
| [Zod](https://github.com/colinhacks/zod) | v4.6.7 — `b500a2ee95134f81d74da7911d7378dd2853d042` | MIT, [ZOD_LICENSE.txt](ZOD_LICENSE.txt) | Copyright (c) 2025 Colin McDonnell |

The Click release uses an annotated tag. Its tag object is `d4ecb8a89a52ad3faacf3f1c65065d08c1d11c14`; the dataset and checkout pin the peeled commit listed above.

Flask and Requests attribution is preserved in [the project's third-party notices](../THIRD_PARTY_NOTICES.md), with [FLASK_LICENSE.txt](FLASK_LICENSE.txt) and [REQUESTS_LICENSE.txt](REQUESTS_LICENSE.txt).

The pinned Zod `packages/zod/src/v4/core/visit.ts` also carries this source-level
attribution, which remains attached to any exported snippet that includes it:

```text
// Traversal pattern adapted from Jaen's v3 `mapOnSchema` (Apache-2.0): https://gist.github.com/jaens/7e15ae1984bb338c86eb5e452dee3010
```

This notice records the upstream attribution; it does not change the Zod
repository license or imply that the linked external source is indexed.

## Jinja multi-file retrieval holdout

[Pallets Jinja](https://github.com/pallets/jinja) 3.1.6 is pinned to peeled commit
`15206881c006c79667fe5154fe80c01c65410679`. Its annotated tag object is
`2d4ce43010630478ee88b463f731389fa18953f4`. Indexed source and published result
excerpts retain its BSD-3-Clause license and **Copyright 2007 Pallets** notice.
The complete upstream license is copied verbatim in
[JINJA_LICENSE.txt](JINJA_LICENSE.txt), SHA-256
`3b49dcee4105eb37bac10faf1be260408fe85d252b8e9df2e0979fc1e094437b`.
No separate upstream `NOTICE` file is present in this pinned checkout. The corpus
is downloaded into ignored local storage rather than committed as a source tree.

## urllib3 response-selection holdout

[urllib3](https://github.com/urllib3/urllib3) 2.5.0 is pinned to peeled commit
`aaab4eccc10c965897540b21e15f11859d0b62e7`; annotated tag object
`e6e91da8dbf0e4efec05cb9839608a787fc11e10`. Its root MIT license is copied
verbatim in [URLLIB3_LICENSE.txt](URLLIB3_LICENSE.txt), SHA-256
`130e3a64d5fdd5d096a752694634a7d9df284469de86e5732100268041e3d686`, including
**Copyright (c) 2008-2020 Andrey Petrov and contributors.** No separate upstream
NOTICE file is tracked. The corpus is downloaded into ignored local storage.

The indexed `src/urllib3/util/ssl_match_hostname.py` explicitly retains a
[Python Software Foundation license](https://docs.python.org/3/license.html)
notice for its standard-library origin. The original source-level notice is:

```text
# Note: This file is under the PSF license as the code comes from the python
# stdlib.   http://docs.python.org/3/license.html
# It is modified to remove commonName support.
```

Published excerpts from that file retain this applicable source-level notice;
the root MIT attribution does not replace it.
