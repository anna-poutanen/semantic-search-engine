# Complementary context v2: observed development replay

Qualification:

```json
{
  "qualified": false,
  "against_controls": {
    "legacy": {
      "group_losses": [],
      "complete_losses": [],
      "single_file_first_hit_losses": [],
      "complete_multi_gains": [
        {
          "subset": "native_jinja_observed",
          "dataset": "jinja",
          "query_id": "jinja_bytecode_reject_and_recompile",
          "condition": "dense"
        },
        {
          "subset": "qwen_development_observed",
          "dataset": "starlette",
          "query_id": "starlette_exception_registration_and_dispatch",
          "condition": "hybrid_k24_b1"
        },
        {
          "subset": "qwen_development_observed",
          "dataset": "zod",
          "query_id": "zod_metadata_attachment_registry",
          "condition": "hybrid_k24_b1"
        }
      ]
    },
    "coverage_control": {
      "group_losses": [
        {
          "subset": "tanstack_observed",
          "dataset": "tanstack",
          "query_id": "tanstack_mutation_scope_serial_continuation",
          "condition": "dense",
          "groups": [
            1,
            3
          ]
        }
      ],
      "complete_losses": [
        {
          "subset": "tanstack_observed",
          "dataset": "tanstack",
          "query_id": "tanstack_mutation_scope_serial_continuation",
          "condition": "dense"
        }
      ],
      "single_file_first_hit_losses": [],
      "complete_multi_gains": [
        {
          "subset": "native_jinja_observed",
          "dataset": "jinja",
          "query_id": "jinja_bytecode_reject_and_recompile",
          "condition": "dense"
        }
      ]
    }
  },
  "additional_distinct_complete_multi_queries_vs_coverage": [
    [
      "jinja",
      "jinja_bytecode_reject_and_recompile"
    ]
  ],
  "fresh_native_required_if_qualified": true,
  "automatic_default_change": false
}
```

All individual changes and strata are retained in summary.json and the streamed ledger. Historical negative arms are immutable references, never newly rerun outcomes. No native latency, fresh generalization, coding correctness or provider-token saving is claimed.
