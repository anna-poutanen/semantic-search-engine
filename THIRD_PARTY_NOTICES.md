# Third-party notices

The project's original code and documentation are licensed under the MIT
license in [LICENSE](LICENSE), copyright (c) 2026 Anna Poutanen. Third-party
code, source excerpts, model weights, and dependencies retain their own
licenses and notices described below. The project's MIT license does not
replace those licenses.

The Python package contains this project's runtime source and license files.
The VS Code extension package contains this project's editor adapter and its
MIT license. Neither package includes model weights, benchmark corpus
checkouts, or the separately installed Python model runtimes. In particular,
the optional `mlx-embeddings` dependency retains GPL-3.0-or-later; it is not
relicensed by this project.

## Pallets Flask

The benchmark refers to Flask source at commit
`d086db856be187255b8ec61ef409357393020f32`. Committed judgment records may contain
source excerpts from that revision. These excerpts remain covered by Flask's
BSD-3-Clause license, reproduced in `benchmarks/FLASK_LICENSE.txt`.

Source: https://github.com/pallets/flask

## Requests

The source-reviewed evaluation refers to Requests at commit
`611c6162cbc4ac2020a2f91c7cfa4f3abf9bbb60`. Indexed source and committed evaluation
artifacts may contain excerpts from that revision. Requests source remains
covered by Apache License 2.0, reproduced in
[benchmarks/REQUESTS_LICENSE.txt](benchmarks/REQUESTS_LICENSE.txt). The ignored
corpus checkout is not redistributed as a second source tree.

Source: [psf/requests](https://github.com/psf/requests)

The upstream `NOTICE` is preserved verbatim:

```text
Requests
Copyright 2019 Kenneth Reitz
```

## Expanded frozen benchmark corpora

Click, Starlette, and Rich checkouts are downloaded separately. Published search
results and model judgments may contain source excerpts from the pinned releases.
Their source licenses, copyright notices, and exact revisions are preserved in
[benchmarks/CORPUS_NOTICES.md](benchmarks/CORPUS_NOTICES.md):

| Corpus | Pinned release and commit | License copy |
|---|---|---|
| [Click](https://github.com/pallets/click) | 8.1.8, `934813e4d421071a1b3db3973c02fe2721359a6e` | BSD-3-Clause, [CLICK_LICENSE.txt](benchmarks/CLICK_LICENSE.txt) |
| [Starlette](https://github.com/encode/starlette) | 0.45.3, `4d72fd87ea1c358691a19468168f7217d8ca6a87` | BSD-3-Clause, [STARLETTE_LICENSE.txt](benchmarks/STARLETTE_LICENSE.txt) |
| [Rich](https://github.com/Textualize/rich) | 13.9.4, `43d3b04725ab9731727fb1126e35980c62f32377` | MIT, [RICH_LICENSE.txt](benchmarks/RICH_LICENSE.txt) |
| [Express](https://github.com/expressjs/express) | v5.3.0, `cf6722bf89a86905281d9160b6482e08166b5ef0` | MIT, [EXPRESS_LICENSE.txt](benchmarks/EXPRESS_LICENSE.txt) |
| [Zod](https://github.com/colinhacks/zod) | v4.6.7, `b500a2ee95134f81d74da7911d7378dd2853d042` | MIT, [ZOD_LICENSE.txt](benchmarks/ZOD_LICENSE.txt) |

No separate upstream `NOTICE` file is present in these pinned releases. The
ignored corpus checkouts are not redistributed as additional source trees.

Zod's `packages/zod/src/v4/core/visit.ts` credits a traversal pattern adapted
from Jaen's v3 `mapOnSchema` under Apache-2.0, with the original source link.
That source-level notice is retained in [the corpus notices](benchmarks/CORPUS_NOTICES.md).

Later source-reviewed fixtures retain their own pinned notices:

| Corpus | Pinned release or commit | License copy |
|---|---|---|
| [Koa](https://github.com/koajs/koa) | `2fecd029c6ce6b79d65158d28a76538f7f6b0378` | MIT, [Koa notice](results/koa-clause-preflight-2026-10-13/NOTICE.md) |
| [HTTPX](https://github.com/encode/httpx) | 0.28.1, `26d48e0634e6ee9cdc0533996db289ce4b430177` | BSD-3-Clause, [HTTPX_LICENSE.txt](benchmarks/HTTPX_LICENSE.txt) and [corpus notice](benchmarks/HTTPX_CORPUS_NOTICE.md) |
| [Django](https://github.com/django/django) | 5.2, `9e7cc2b628fe8fd3895986af9b7fc9525034c1b0` | BSD-3-Clause, [DJANGO_LICENSE.txt](results/django-index-preflight-2026-10-14/DJANGO_LICENSE.txt) |
| [Werkzeug](https://github.com/pallets/werkzeug) | 3.1.3, `6389612fd1ee1bd93579eed5026e8fd471d04abd` | BSD-3-Clause, [Werkzeug notice](results/werkzeug-context-preflight-2026-10-14/NOTICE.md) |

Published inventories, source snapshots and result excerpts retain these
upstream notices. The ignored checkouts are downloaded separately.

## H2O-Lightning and the community MLX runtime

The weights and runtime are downloaded separately and not redistributed here.
Their upstream license is Apache-2.0. Source, model revisions, and environment
details are recorded in experiment output.

- https://huggingface.co/h2oai/h2o-lightning-4b
- https://huggingface.co/netprojects/h2o-lightning-4b-mlx-q8
- https://github.com/internet-projects/h2o-lightning-mlx

## Qwen3 embeddings and MLX embedding runtime

Original Qwen3-Embedding-0.6B weights are downloaded separately at revision
`97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3` and are not redistributed here.
The model is licensed under Apache-2.0:
https://huggingface.co/Qwen/Qwen3-Embedding-0.6B

The optional Mac dependency `mlx-embeddings==0.1.0` is licensed under
GPL-3.0-or-later, copyright 2024 Prince Canuma. Its package includes its license;
the dependency's source is not vendored in this repository:
https://github.com/Blaizzy/mlx-embeddings

## Optional Qwen3 reranker

Original Qwen3-Reranker-0.6B weights are downloaded separately at revision
`e61197ed45024b0ed8a2d74b80b4d909f1255473` and are not redistributed here.
The model retains its Apache-2.0 license:
https://huggingface.co/Qwen/Qwen3-Reranker-0.6B

The optional MLX and MLX LM runtimes are installed separately, retaining their
upstream license files:
https://github.com/ml-explore/mlx and https://github.com/ml-explore/mlx-lm

## Persistent retrieval, context counting, and MCP

These Python dependencies are installed separately; their sources are not
vendored in this repository. Their upstream license files accompany the installed
packages or upstream distributions:

- [`sqlite-vec==0.1.9`](https://github.com/asg017/sqlite-vec/tree/v0.1.9) is
  dual licensed under MIT or Apache-2.0. Its bundled SQLite extension supplies
  exact cosine distance over persisted vectors.
- [`tiktoken==0.14.0`](https://github.com/openai/tiktoken) is MIT licensed,
  copyright (c) 2022 OpenAI, Shantanu Jain. It counts the compact context payload
  with `cl100k_base`; this count is separate from a coding agent's billing.
- The optional [`mcp==2.3.0`](https://github.com/modelcontextprotocol/python-sdk)
  Python SDK is MIT licensed, copyright (c) 2024 Anthropic, PBC. It provides the
  stdio adapter and official-client protocol tests.

SQLite FTS5 is supplied by the Python SQLite build. SQLite is in the
[public domain](https://www.sqlite.org/copyright.html); no separate FTS5 extension
is downloaded by this project.
