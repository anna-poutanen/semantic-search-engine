"""A structural certificate that extra clause inference cannot change context."""
from __future__ import annotations

from semantic_search.semantic_context import protected_final_rows
from scripts.research_role_context import _rows, apply_role_guard

from experiments.dependency_context.guard import apply_dependency_guard


def skip_certificate(reference, raw_rows, *, dependencies, definition_keys,
                     precoalesce_rows=(), max_results=8, cancel_check=lambda: None):
    """Certify exact coverage when every full-context victim is protected.

    V1 replaces only unprotected rows (it cannot append to a full context).
    Both terminal guards reject the whole proposal if any of their protected
    baseline dictionaries change. If their union covers every possible victim,
    a changed proposal cannot survive. No semantic feature or query routing is
    used to issue this certificate. Fewer than max_results rows never qualifies.
    """
    cancel_check()
    if type(max_results) is not int or max_results < 1:
        raise ValueError("Positive excerpt cap required")
    receipt = {"schema": "dependency_skip_certificate_v1", "eligible": False,
        "reason": None, "max_results": max_results, "semantic_protected_ids": [],
        "role_protected_ids": [], "pair_protected_ids": [], "unprotected_ids": [],
        "calls": {name: 0 for name in ("model", "retrieval", "index", "v1", "coalesce", "backfill")}}
    rows = _rows(reference)
    if rows is None:
        receipt["reason"] = "invalid_reference"
        return receipt
    if len(rows) != max_results:
        receipt["reason"] = "context_not_full"
        return receipt
    _, role = apply_role_guard(reference, reference, definition_keys=definition_keys, cancel_check=cancel_check)
    _, pair = apply_dependency_guard(reference, reference, dependencies=dependencies,
        definition_keys=definition_keys, cancel_check=cancel_check)
    if not role["accepted"] or not pair["accepted"]:
        receipt["reason"] = "invalid_protection_facts"
        return receipt
    semantic, _ = protected_final_rows(reference, raw_rows, definition_keys, max_results, precoalesce_rows)
    receipt["semantic_protected_ids"] = [row["id"] for row in rows if row["id"] in semantic]
    receipt["role_protected_ids"] = list(role["protected_ids"])
    receipt["pair_protected_ids"] = list(pair["protected_ids"])
    protected = set(semantic) | set(role["protected_ids"]) | set(pair["protected_ids"])
    receipt["unprotected_ids"] = [row["id"] for row in rows if row["id"] not in protected]
    receipt["eligible"] = not receipt["unprotected_ids"]
    receipt["reason"] = "all_full_context_rows_protected" if receipt["eligible"] else "unprotected_victim_exists"
    cancel_check()
    return receipt
