# Third-party notices

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
