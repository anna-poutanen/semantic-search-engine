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
