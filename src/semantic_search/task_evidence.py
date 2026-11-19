"""Research-only task-evidence selection from one frozen retrieval result.

This module is deliberately outside the production search contract.  It consumes
only a resolved evidence request, the already ranked candidate prefix, canonical
definition identities, and the existing conservative relation graph.  Evaluator
labels and benchmark-specific paths are neither accepted nor inferred.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import PurePosixPath

from semantic_search.context import (
    ENCODING,
    compact_result,
    count_context,
    encoding,
    serialize_context,
)
from semantic_search.selection import definition_context
from semantic_search.source import physical_lines


MODE = "task_evidence_v1"
PLAN_SCHEMA = "task_evidence_plan_v1"
PACKING_SCHEMA = "task_evidence_packing_v1"
MAX_CANDIDATES = 50
MAX_TRIALS = 8

SUPPORTED_NEEDS = (
    "implementation",
    "callers",
    "tests",
    "behavioral_constraints",
)

GAP_REASONS = (
    "unsupported_source_kind",
    "unsupported_artifact_kind",
    "no_extractable_window",
    "outside_indexed_scope",
    "not_retrieved",
    "selection_excluded",
    "relation_unresolved",
    "result_limit_exhausted",
    "source_budget_exhausted",
    "token_budget_exhausted",
)


class PlannedPairs(list):
    """An ordinary eligible sequence carrying a defensive JSON plan copy."""

    __slots__ = ("_source_plan_json",)

    def __init__(self, values, source_plan):
        super().__init__(values)
        self._source_plan_json = json.dumps(
            source_plan, sort_keys=True, ensure_ascii=False, allow_nan=False
        )

    @property
    def source_plan(self):
        return json.loads(self._source_plan_json)


def validate_needed_evidence(value):
    """Return a stable tuple of supported, nonduplicated evidence needs."""
    if not isinstance(value, (list, tuple)):
        raise ValueError("needed_evidence must be a list or tuple")
    if not value:
        raise ValueError("needed_evidence must be nonempty")
    if any(type(item) is not str for item in value):
        raise ValueError("needed_evidence entries must be strings")
    unknown = [item for item in value if item not in SUPPORTED_NEEDS]
    if unknown:
        raise ValueError(f"Unsupported evidence need: {unknown[0]}")
    if len(set(value)) != len(value):
        raise ValueError("needed_evidence entries must be unique")
    return tuple(value)


def _test_path(path):
    if type(path) is not str or not path or "\\" in path:
        return False
    value = PurePosixPath(path)
    parts = tuple(part.lower() for part in value.parts)
    name = value.name.lower()
    stem = value.stem.lower()
    return (
        bool({"test", "tests"}.intersection(parts))
        or name == "conftest.py"
        or name.startswith("test_")
        or stem.endswith("_test")
    )


def infer_evidence_roles(path, *, caller=False):
    """Infer conservative artifact roles from a path and a verified graph edge.

    Tests are executable behavioral examples, but neither tests nor ordinary
    implementations are treated as proof that a requested behavior is complete.
    ``caller`` must be supplied only after unique-definition graph validation.
    """
    if type(caller) is not bool:
        raise ValueError("caller must be a boolean")
    roles = set()
    if _test_path(path):
        roles.update(("tests", "behavioral_constraints"))
    else:
        roles.add("implementation")
    if caller:
        roles.add("callers")
    return tuple(role for role in SUPPORTED_NEEDS if role in roles)


def _canonical_key(key):
    if (
        not isinstance(key, (tuple, list))
        or len(key) not in (4, 5)
        or not all(type(value) is str and value for value in key[:2])
        or type(key[2]) is not int
        or type(key[3]) is not int
        or not 1 <= key[2] <= key[3]
        or (len(key) == 5 and (type(key[4]) is not int or key[4] < 0))
    ):
        return None
    return tuple(key)


def _definition_identity(item, definition_keys):
    key = _canonical_key((definition_keys or {}).get(item.id))
    return ("definition", *key) if key is not None else ("window", item.id)


def _unique_definitions(definition_keys, ambiguous_symbols):
    grouped = {}
    for value in (definition_keys or {}).values():
        key = _canonical_key(value)
        if key is not None:
            grouped.setdefault(key[:2], set()).add(key)
    ambiguous = set(ambiguous_symbols or ())
    return {
        label: next(iter(keys))
        for label, keys in grouped.items()
        if len(keys) == 1 and label not in ambiguous
    }


def _caller_witnesses(item, anchors, references, definition_keys, unique_definitions):
    """Return exact candidate-to-anchor witnesses for unique static definitions."""
    key = _canonical_key((definition_keys or {}).get(item.id))
    label = (item.path, item.symbol)
    if key is None or unique_definitions.get(label) != key:
        return []
    edges = getattr(references, "edges", {}) if references is not None else {}
    if not hasattr(edges, "get"):
        return []
    targets = {
        tuple(target)
        for target in edges.get(label, ())
        if isinstance(target, (tuple, list)) and len(target) == 2
    }
    witnesses = []
    for anchor, _ in anchors:
        anchor_key = _canonical_key((definition_keys or {}).get(anchor.id))
        anchor_label = (anchor.path, anchor.symbol)
        if (
            anchor_key is not None
            and unique_definitions.get(anchor_label) == anchor_key
            and anchor_label in targets
            and anchor_label != label
        ):
            witnesses.append(
                {
                    "candidate_id": item.id,
                    "candidate_key": list(key),
                    "anchor_id": anchor.id,
                    "anchor_key": list(anchor_key),
                    "source": list(label),
                    "target": list(anchor_label),
                }
            )
    return sorted(witnesses, key=lambda row: (row["anchor_id"], row["target"]))


def task_evidence_plan(
    pairs,
    *,
    needed_evidence,
    query,
    max_results,
    include_dependencies,
    references,
    definition_keys=None,
    ambiguous_symbols=None,
):
    """Plan one bounded complementary exchange without changing the baseline.

    The returned eligible list is the exact definition-context baseline.  The
    post-packer uses the attached plan only after constructing the exact control.
    """
    requested = validate_needed_evidence(needed_evidence)
    ranked = list(pairs)[:MAX_CANDIDATES]
    selected, baseline, origins = definition_context(
        ranked,
        query=query,
        max_results=max_results,
        include_dependencies=include_dependencies,
        references=references,
        definition_keys=definition_keys,
        ambiguous_symbols=ambiguous_symbols,
    )
    anchors = selected[: min(5, max_results)]
    dependency_owners = {
        item.id: origins[item.id][1]
        for item, _ in baseline
        if origins.get(item.id, ("retrieval", None))[0] == "dependency"
    }
    protected = {
        *(item.id for item, _ in anchors),
        *dependency_owners,
        *(owner for owner in dependency_owners.values() if owner is not None),
    }
    protected_windows = []
    seen_windows = set()
    for item, _ in [*anchors, *baseline]:
        if item.id not in protected or item.id in seen_windows:
            continue
        seen_windows.add(item.id)
        protected_windows.append(
            {
                "id": item.id,
                "path": item.path,
                "start_line": item.start_line,
                "end_line": item.end_line,
                "content_hash": item.content_hash,
            }
        )

    distinct = []
    raw_ranks = {}
    identities = set()
    for rank, (item, score) in enumerate(ranked, 1):
        raw_ranks.setdefault(item.id, rank)
        identity = _definition_identity(item, definition_keys)
        if identity in identities:
            continue
        identities.add(identity)
        distinct.append((rank, item, score, identity))

    baseline_identities = {
        _definition_identity(item, definition_keys) for item, _ in baseline
    }
    roles = {}
    caller_witnesses = {}
    canonical_keys = {}
    unique_definitions = _unique_definitions(definition_keys, ambiguous_symbols)
    for _, item, _, _ in distinct:
        witnesses = _caller_witnesses(
            item, anchors, references, definition_keys, unique_definitions
        )
        item_roles = infer_evidence_roles(item.path, caller=bool(witnesses))
        roles[item.id] = list(item_roles)
        if witnesses:
            caller_witnesses[item.id] = witnesses
        key = _canonical_key((definition_keys or {}).get(item.id))
        if key is not None:
            canonical_keys[item.id] = list(key)
    for item, _ in baseline:
        roles.setdefault(item.id, list(infer_evidence_roles(item.path)))
        key = _canonical_key((definition_keys or {}).get(item.id))
        if key is not None:
            canonical_keys.setdefault(item.id, list(key))

    candidates = []
    for rank, item, _, identity in distinct:
        item_roles = roles[item.id]
        if identity in baseline_identities or not set(item_roles).intersection(
            requested
        ):
            continue
        witnesses = caller_witnesses.get(item.id, [])
        candidates.append(
            {
                "id": item.id,
                "path": item.path,
                "raw_rank": rank,
                "roles": item_roles,
                "caller_witnesses": deepcopy(witnesses),
            }
        )
    candidates.sort(
        key=lambda row: (not bool(row["caller_witnesses"]), row["raw_rank"], row["id"])
    )
    receipt = {
        "schema": PLAN_SCHEMA,
        "mode": MODE,
        "requested": list(requested),
        "candidate_limit": MAX_CANDIDATES,
        "trial_limit": MAX_TRIALS,
        "anchor_ids": [item.id for item, _ in anchors],
        "dependency_owners": dependency_owners,
        "protected_ids": [item.id for item, _ in baseline if item.id in protected],
        "protected_windows": protected_windows,
        "baseline_eligible_ids": [item.id for item, _ in baseline],
        "raw_ranks": raw_ranks,
        "canonical_keys": canonical_keys,
        "roles": roles,
        "caller_witnesses": caller_witnesses,
        "candidate_ids": [row["id"] for row in candidates],
        "candidates": candidates,
    }
    return selected, PlannedPairs(baseline, receipt), origins, deepcopy(receipt)


def task_evidence_context(pairs, **kwargs):
    """Selector-shaped wrapper for an explicitly injected research harness."""
    return task_evidence_plan(pairs, **kwargs)[:3]


def _same(left, right):
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return list(left) == list(right) and all(
            _same(left[key], right[key]) for key in left
        )
    if isinstance(left, (list, tuple)):
        return len(left) == len(right) and all(_same(a, b) for a, b in zip(left, right))
    return left == right


def _valid_row(row):
    if not isinstance(row, dict):
        return False
    required = (
        "id",
        "path",
        "symbol",
        "source",
        "source_hash",
        "content_hash",
        "start_line",
        "end_line",
        "window_end_line",
        "truncated",
    )
    if not all(key in row for key in required):
        return False
    if (
        type(row["id"]) is not str
        or not row["id"]
        or type(row["path"]) is not str
        or not row["path"]
        or type(row["source"]) is not str
        or not row["source"]
        or type(row["start_line"]) is not int
        or type(row["end_line"]) is not int
        or type(row["window_end_line"]) is not int
        or type(row["truncated"]) is not bool
        or row["start_line"] < 1
        or row["truncated"]
        or row["end_line"] != row["window_end_line"]
    ):
        return False
    lines = physical_lines(row["source"])
    digest = hashlib.sha256(row["source"].encode("utf-8")).hexdigest()
    return (
        row["end_line"] == row["start_line"] + len(lines) - 1
        and row["source_hash"] == digest
        and row["content_hash"] == digest
    )


def _overlaps(row, window):
    return (
        row.get("path") == window.get("path")
        and type(row.get("start_line")) is int
        and type(row.get("end_line")) is int
        and row["start_line"] <= window.get("end_line", -1)
        and window.get("start_line", 0) <= row["end_line"]
    )


def _protected_reference_rows(reference, plan):
    protected = set(plan["protected_ids"])
    owners = set(plan["dependency_owners"].values())
    windows = plan["protected_windows"]
    return [
        row
        for row in reference["results"]
        if row.get("id") in protected
        or row.get("id") in owners
        or row.get("origin") == "dependency"
        or any(_overlaps(row, window) for window in windows)
    ]


def _protected_preserved(reference_rows, proposal):
    positions = []
    for original in reference_rows:
        matches = [
            index
            for index, current in enumerate(proposal["results"])
            if current.get("id") == original.get("id") and _same(current, original)
        ]
        if len(matches) != 1:
            return False
        positions.append(matches[0])
    return positions == sorted(positions)


def _prefix(row, lines, full_source):
    row["source"] = "".join(lines)
    row["end_line"] = row["start_line"] + len(lines) - 1
    row["source_hash"] = hashlib.sha256(row["source"].encode("utf-8")).hexdigest()
    row["truncated"] = bool(row.get("truncated", False)) or row["source"] != full_source


def _roles_for_row(row, plan):
    roles = set(plan["roles"].get(row.get("id"), infer_evidence_roles(row.get("path"))))
    if plan["caller_witnesses"].get(row.get("id")):
        roles.add("callers")
    return roles


_STATUS_RANK = {"unresolved": 0, "partial": 1, "candidate": 2}


def _need_statuses(response, plan):
    return {
        record["kind"]: record["status"] for record in _need_records(response, plan, {})
    }


def _requested_status_preserved(reference, proposal, plan):
    before = _need_statuses(reference, plan)
    after = _need_statuses(proposal, plan)
    return all(
        _STATUS_RANK[after[need]] >= _STATUS_RANK[status]
        for need, status in before.items()
    )


def _ordered_victims(reference, exchangeable, candidate, plan):
    """Prefer reverse-ranked victims whose control evidence stands without replacement."""
    independent = []
    replacement_backed = []
    for victim in reversed(exchangeable):
        without = {
            **reference,
            "results": [row for row in reference["results"] if row is not victim],
        }
        if _requested_status_preserved(reference, without, plan):
            independent.append(victim)
            continue
        with_replacement = {
            **without,
            "results": [*without["results"], candidate],
        }
        if _requested_status_preserved(reference, with_replacement, plan):
            replacement_backed.append(victim)
    return [*independent, *replacement_backed]


def _need_records(
    response, plan, causes, *, source_budget=None, token_budget=None
):
    records = []
    for need in plan["requested"]:
        matching = [
            (index, row)
            for index, row in enumerate(response["results"])
            if need in _roles_for_row(row, plan)
        ]
        complete = [
            (index, row)
            for index, row in matching
            if not row.get("truncated", False)
            and row.get("end_line") == row.get("window_end_line", row.get("end_line"))
        ]
        if complete:
            records.append(
                {
                    "kind": need,
                    "status": "candidate",
                    "result_indexes": [index for index, _ in complete],
                }
            )
        elif matching:
            cause = deepcopy(causes.get(need, {}))
            reason = cause.pop("reason", None)
            if reason not in GAP_REASONS:
                token_limited = response.get("token_usage", {}).get("truncated")
                reason = ("token_budget_exhausted" if token_limited
                          else "source_budget_exhausted")
                candidate_id = matching[0][1].get("id")
                cap = token_budget if token_limited else source_budget
                if isinstance(candidate_id, str) and candidate_id:
                    cause["candidate_id"] = candidate_id
                if type(cap) is int:
                    cause["cap"] = cap
            records.append(
                {
                    "kind": need,
                    "status": "partial",
                    "result_indexes": [index for index, _ in matching],
                    "reason": reason,
                    **cause,
                }
            )
        else:
            cause = deepcopy(causes.get(need, {}))
            reason = cause.pop("reason", None)
            if reason not in GAP_REASONS:
                if need == "callers" and not any(
                    "callers" in roles for roles in plan["roles"].values()
                ):
                    reason = "relation_unresolved"
                elif not any(need in roles for roles in plan["roles"].values()):
                    reason = "not_retrieved"
                else:
                    reason = "selection_excluded"
            records.append(
                {"kind": need, "status": "unresolved", "reason": reason, **cause}
            )
    return records


def _count_packet(packet):
    usage = packet["token_usage"]
    usage["tokens"] = 0
    tokenizer = encoding()
    for _ in range(12):
        count = len(tokenizer.encode(serialize_context(packet), disallowed_special=()))
        if count == usage["tokens"]:
            return count
        usage["tokens"] = count
    raise RuntimeError("Task-evidence packet token count did not stabilize")


def _research_packet(
    response, plan, causes, source_budget, token_budget, token_clipped=False
):
    packet = deepcopy(compact_result(response))
    packet["task_evidence"] = {
        "requested": list(plan["requested"]),
        "needs": _need_records(
            response,
            plan,
            causes,
            source_budget=source_budget,
            token_budget=token_budget,
        ),
    }
    packet["token_usage"] = {
        "encoding": ENCODING,
        "tokens": 0,
        "budget": token_budget,
        "truncated": bool(
            response.get("token_usage", {}).get("truncated", False) or token_clipped
        ),
    }
    _count_packet(packet)
    return packet


def _finalize_standard(response, token_budget, *, token_clipped=False):
    response["total_source_chars"] = sum(
        len(row["source"]) for row in response["results"]
    )
    response["budget_exhausted"] = bool(
        response.get("budget_exhausted", False)
        or token_clipped
        or any(row.get("truncated", False) for row in response["results"])
    )
    if token_budget is not None or "token_usage" in response:
        previous = response.get("token_usage", {})
        response["token_usage"] = {
            "encoding": ENCODING,
            "tokens": 0,
            "budget": token_budget,
            "truncated": bool(previous.get("truncated", False) or token_clipped),
        }
        count_context(response)
    return response


def _validate_plan(plan):
    if (
        not isinstance(plan, dict)
        or plan.get("schema") != PLAN_SCHEMA
        or plan.get("mode") != MODE
    ):
        raise ValueError("Task-evidence packing requires its actual planner receipt")
    requested = validate_needed_evidence(plan.get("requested"))
    required = {
        "anchor_ids",
        "dependency_owners",
        "protected_ids",
        "protected_windows",
        "roles",
        "caller_witnesses",
        "candidates",
    }
    if not required.issubset(plan) or list(requested) != plan["requested"]:
        raise ValueError("Task-evidence planner receipt is incomplete")
    return requested


def _metadata_trim_trial(result, reference, plan, protected_ids):
    """Trim one whole line, preferring a row that keeps all request statuses."""
    fallback = None
    for index in range(len(result["results"]) - 1, -1, -1):
        if result["results"][index].get("id") in protected_ids:
            continue
        trial = deepcopy(result)
        row = trial["results"][index]
        lines = physical_lines(row["source"])
        if lines:
            lines.pop()
        if lines:
            _prefix(row, lines, row["source"])
        else:
            trial["results"].pop(index)
        fallback = fallback or (index, trial)
        if _requested_status_preserved(reference, trial, plan):
            return index, trial
    return fallback


def _fallback_packet(
    reference,
    plan,
    causes,
    source_budget,
    token_budget,
    receipt,
    reason,
    protected_rows,
    cancel_check,
):
    result = deepcopy(reference)
    token_clipped = False
    clipped_ids = []
    while True:
        cancel_check()
        _finalize_standard(result, token_budget, token_clipped=token_clipped)
        packet = _research_packet(
            result,
            plan,
            causes,
            source_budget,
            token_budget,
            token_clipped=token_clipped,
        )
        if token_budget is None or packet["token_usage"]["tokens"] <= token_budget:
            break
        protected_ids = {row.get("id") for row in protected_rows}
        trimming = _metadata_trim_trial(result, reference, plan, protected_ids)
        if trimming is None:
            raise ValueError(
                "token_budget cannot contain protected control source and task-evidence metadata"
            )
        index, trial = trimming
        old = result["results"][index]
        for need in _roles_for_row(old, plan).intersection(plan["requested"]):
            causes.setdefault(
                need,
                {
                    "reason": "token_budget_exhausted",
                    "candidate_id": old.get("id"),
                    "cap": token_budget,
                },
            )
        token_clipped = True
        if old.get("id") not in clipped_ids:
            clipped_ids.append(old.get("id"))
        result = trial
    if not _protected_preserved(protected_rows, result):
        raise ValueError("Fallback packet changed protected control source")
    receipt.update(
        accepted=False,
        candidate_id=None,
        victim_id=None,
        fallback_reason=reason,
        packet_tokens=packet["token_usage"]["tokens"],
        metadata_token_clipped_ids=clipped_ids,
    )
    return result, packet, receipt


def pack_task_evidence(
    response,
    candidates,
    *,
    selection_plan,
    ranked_candidates,
    reference_factory,
    max_results,
    source_budget,
    token_budget=None,
    cancel_check=lambda: None,
    definition_keys=None,
    **ignored,
):
    """Pack one complementary candidate and return response, packet, and receipt.

    The standard response never contains research checklist metadata.  The packet
    extends its compact projection and independently counts the whole delivered
    payload, so checklist prose cannot escape the token budget.
    """
    del response, candidates, definition_keys, ignored
    if (
        type(max_results) is not int
        or max_results < 0
        or type(source_budget) is not int
        or source_budget < 0
        or (
            token_budget is not None
            and (type(token_budget) is not int or token_budget < 0)
        )
    ):
        raise ValueError("Context limits must be nonnegative integers")
    _validate_plan(selection_plan)
    if not callable(reference_factory):
        raise ValueError("Task-evidence packing requires an exact control factory")
    cancel_check()
    reference = reference_factory()
    cancel_check()
    if not isinstance(reference, dict) or not isinstance(
        reference.get("results"), list
    ):
        raise ValueError("Exact control factory returned an invalid response")
    reference = deepcopy(reference)
    protected_rows = _protected_reference_rows(reference, selection_plan)
    protected_ids = [row.get("id") for row in protected_rows]
    receipt = {
        "schema": PACKING_SCHEMA,
        "mode": MODE,
        "requested": list(selection_plan["requested"]),
        "trial_limit": MAX_TRIALS,
        "protected_ids": protected_ids,
        "trials": [],
        "accepted": False,
        "candidate_id": None,
        "victim_id": None,
        "fallback_reason": None,
        "packet_tokens": None,
        "metadata_token_clipped_ids": [],
    }

    ranked = []
    seen_ranked = set()
    for row in ranked_candidates:
        if len(ranked) >= MAX_CANDIDATES:
            break
        if not isinstance(row, dict) or row.get("id") in seen_ranked:
            continue
        seen_ranked.add(row.get("id"))
        ranked.append(deepcopy(row))
    by_id = {row.get("id"): row for row in ranked}
    control_paths = {row.get("path") for row in reference["results"]}
    requested = set(selection_plan["requested"])
    eligible_records = [
        record
        for record in selection_plan["candidates"]
        if isinstance(record, dict)
        and record.get("id") in by_id
        and by_id[record["id"]].get("path") == record.get("path")
        and record.get("path") not in control_paths
        and requested.intersection(record.get("roles", ()))
    ]
    eligible_records.sort(
        key=lambda row: (
            not bool(row.get("caller_witnesses")),
            row.get("raw_rank", MAX_CANDIDATES + 1),
            row.get("id", ""),
        )
    )
    protected_set = set(protected_ids)
    causes = {}
    control_is_full = len(reference["results"]) >= max_results
    exchangeable = [
        row
        for row in reference["results"]
        if row.get("origin", "retrieval") == "retrieval"
        and row.get("id") not in protected_set
    ]
    if eligible_records and control_is_full and not exchangeable:
        statuses = _need_statuses(reference, selection_plan)
        for need in requested:
            relevant = next(
                (
                    record
                    for record in eligible_records
                    if need in record.get("roles", ())
                ),
                None,
            )
            if statuses[need] != "candidate" and relevant is not None:
                causes[need] = {
                    "reason": "result_limit_exhausted",
                    "candidate_id": relevant["id"],
                    "cap": max_results,
                }
        return _fallback_packet(
            reference,
            selection_plan,
            causes,
            source_budget,
            token_budget,
            receipt,
            "no_exchangeable_result",
            protected_rows,
            cancel_check,
        )
    if not eligible_records:
        return _fallback_packet(
            reference,
            selection_plan,
            causes,
            source_budget,
            token_budget,
            receipt,
            "no_complementary_candidate",
            protected_rows,
            cancel_check,
        )

    for record in eligible_records:
        if len(receipt["trials"]) >= MAX_TRIALS:
            break
        cancel_check()
        raw_candidate = deepcopy(by_id[record["id"]])
        candidate_roles = set(record["roles"]).intersection(requested)

        def new_trial(victim=None):
            return {
                "candidate_id": record["id"],
                "raw_rank": record.get("raw_rank"),
                "roles": list(record.get("roles", ())),
                "victim_id": victim.get("id") if victim is not None else None,
                "accepted": False,
                "reason": None,
                "source_clipped": False,
                "token_clipped": False,
            }

        if not _valid_row(raw_candidate):
            trial_record = new_trial()
            trial_record["reason"] = "invalid_candidate_source"
            receipt["trials"].append(trial_record)
            continue
        victims = (
            _ordered_victims(reference, exchangeable, raw_candidate, selection_plan)
            if control_is_full
            else [None]
        )
        if not victims:
            trial_record = new_trial()
            trial_record["reason"] = "requested_role_coverage"
            receipt["trials"].append(trial_record)
            for need in candidate_roles:
                if not any(
                    need in _roles_for_row(row, selection_plan)
                    for row in reference["results"]
                ):
                    causes.setdefault(
                        need,
                        {
                            "reason": "result_limit_exhausted",
                            "candidate_id": record["id"],
                            "cap": max_results,
                        },
                    )
            continue

        for victim in victims:
            if len(receipt["trials"]) >= MAX_TRIALS:
                break
            cancel_check()
            trial_record = new_trial(victim)
            receipt["trials"].append(trial_record)
            trial_causes = deepcopy(causes)
            proposal = deepcopy(reference)
            if victim is not None:
                proposal["results"] = [
                    row
                    for row in proposal["results"]
                    if row.get("id") != victim.get("id")
                ]
            candidate = deepcopy(raw_candidate)
            candidate["origin"] = "retrieval"
            candidate["related_to"] = None
            full_source = candidate["source"]
            remaining = source_budget - sum(
                len(row["source"]) for row in proposal["results"]
            )
            lines, used = [], 0
            for line in physical_lines(full_source):
                cancel_check()
                if used + len(line) > remaining:
                    break
                lines.append(line)
                used += len(line)
            if not lines:
                trial_record["reason"] = "source_budget_exhausted"
                for need in candidate_roles:
                    causes[need] = {
                        "reason": "source_budget_exhausted",
                        "candidate_id": record["id"],
                        "cap": source_budget,
                    }
                continue
            _prefix(candidate, lines, full_source)
            trial_record["source_clipped"] = candidate["source"] != full_source
            if trial_record["source_clipped"]:
                for need in candidate_roles:
                    cause = {
                        "reason": "source_budget_exhausted",
                        "candidate_id": record["id"],
                        "cap": source_budget,
                    }
                    trial_causes[need] = cause
                    causes.setdefault(need, cause)
                if candidate_roles.intersection(("implementation", "callers")):
                    trial_record["reason"] = "source_budget_exhausted"
                    continue
            proposal["results"].append(candidate)

            token_clipped = False
            while True:
                cancel_check()
                if token_clipped:
                    for need in candidate_roles:
                        cause = {
                            "reason": "token_budget_exhausted",
                            "candidate_id": record["id"],
                            "cap": token_budget,
                        }
                        trial_causes[need] = cause
                        causes.setdefault(need, cause)
                _finalize_standard(proposal, token_budget, token_clipped=token_clipped)
                packet = _research_packet(
                    proposal,
                    selection_plan,
                    trial_causes,
                    source_budget,
                    token_budget,
                    token_clipped=token_clipped,
                )
                if (
                    token_budget is None
                    or packet["token_usage"]["tokens"] <= token_budget
                ):
                    break
                token_clipped = True
                trial_record["token_clipped"] = True
                lines.pop()
                if not lines:
                    break
                _prefix(candidate, lines, full_source)
            if not lines:
                trial_record["reason"] = "token_budget_exhausted"
                for need in candidate_roles:
                    causes[need] = {
                        "reason": "token_budget_exhausted",
                        "candidate_id": record["id"],
                        "cap": token_budget,
                    }
                continue

            if token_clipped and candidate_roles.intersection(
                ("implementation", "callers")
            ):
                trial_record["reason"] = "token_budget_exhausted"
                continue
            if not _requested_status_preserved(reference, proposal, selection_plan):
                trial_record["reason"] = "requested_role_status_degraded"
                continue

            valid = (
                len(proposal["results"]) <= max_results
                and proposal["total_source_chars"] <= source_budget
                and _protected_preserved(protected_rows, proposal)
                and proposal["results"][-1].get("id") == record["id"]
                and proposal["results"][-1].get("path") not in control_paths
                and bool(candidate_roles)
                and (
                    token_budget is None
                    or packet["token_usage"]["tokens"] <= token_budget
                )
            )
            if not valid:
                trial_record["reason"] = "protected_source_or_budget_guard"
                continue
            trial_record["accepted"] = True
            receipt.update(
                accepted=True,
                candidate_id=record["id"],
                victim_id=victim.get("id") if victim is not None else None,
                fallback_reason=None,
                packet_tokens=packet["token_usage"]["tokens"],
            )
            return proposal, packet, receipt

    reason = "candidate_trials_exhausted"
    return _fallback_packet(
        reference,
        selection_plan,
        causes,
        source_budget,
        token_budget,
        receipt,
        reason,
        protected_rows,
        cancel_check,
    )
