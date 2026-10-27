"""Research-only graph-neighbor allocation with post-packing source guards.

The graph supplies conservative static relationships, never proof that an
implementation answers a query. These hooks are deliberately not the defaults.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import hashlib
import json

from semantic_search.retrieval import multifile_clauses
from semantic_search.selection import _definition_key, definition_context
from semantic_search.source import physical_lines


MODES = ("source_graph", "source_graph_all")
MAX_ADDITIONS = 2
_PROVENANCE = ("source_origin", "buffer_version", "buffer_hash")


class PlannedPairs(list):
    """An ordinary eligible sequence carrying one defensive research receipt."""

    __slots__ = ("_source_plan_json",)

    def __init__(self, values, source_plan):
        super().__init__(values)
        self._source_plan_json = json.dumps(source_plan, sort_keys=True,
                                           ensure_ascii=False, allow_nan=False)

    @property
    def source_plan(self):
        return json.loads(self._source_plan_json)


def _canonical_key(key):
    if (not isinstance(key, (tuple, list)) or len(key) not in (4, 5)
            or not all(isinstance(value, str) for value in key[:2])
            or type(key[2]) is not int or type(key[3]) is not int
            or not 1 <= key[2] <= key[3]
            or (len(key) == 5 and (type(key[4]) is not int or key[4] < 0))):
        return None
    return tuple(key)


def _graph_witnesses(anchors, references, definition_keys, ambiguous_symbols):
    """Read one-hop existing edges only when both actual definitions are unique."""
    definitions = defaultdict(set)
    for value in (definition_keys or {}).values():
        key = _canonical_key(value)
        if key is not None:
            definitions[key[:2]].add(key)
    ambiguous = set(ambiguous_symbols or ())
    unique = {label: next(iter(keys)) for label, keys in definitions.items()
              if len(keys) == 1 and label not in ambiguous}
    edges = getattr(references, "edges", {}) if references is not None else {}
    if not hasattr(edges, "items"):
        return []
    # Sorting identities affects receipts only. Candidate choices use raw rank.
    valid_edges = set()
    for source, targets in edges.items():
        if not isinstance(source, tuple) or source not in unique:
            continue
        for target in targets:
            target = tuple(target) if isinstance(target, (tuple, list)) else None
            if target in unique and target != source:
                valid_edges.add((source, target))
    output = []
    for anchor, _ in anchors:
        key = _canonical_key((definition_keys or {}).get(anchor.id))
        label = (anchor.path, anchor.symbol)
        if key is None or unique.get(label) != key:
            continue
        for source, target in sorted(valid_edges):
            if label == source:
                direction, neighbor = "callee", target
            elif label == target:
                direction, neighbor = "caller", source
            else:
                continue
            output.append({"anchor_id": anchor.id, "anchor_key": list(key),
                           "direction": direction, "neighbor_key": list(unique[neighbor]),
                           "source_key": list(unique[source]), "target_key": list(unique[target])})
    return output


def source_allocation_plan(pairs, *, mode, query, max_results, include_dependencies,
                           references, definition_keys=None, ambiguous_symbols=None):
    """Return a bounded graph proposal without receiving labels or evidence spans."""
    if mode not in MODES:
        raise ValueError("Unknown source allocation mode")
    pairs = list(pairs)
    selected, baseline, origins = definition_context(
        pairs, query=query, max_results=max_results, include_dependencies=include_dependencies,
        references=references, definition_keys=definition_keys, ambiguous_symbols=ambiguous_symbols)
    anchors = selected[:min(5, max_results)]
    clauses = multifile_clauses(query)
    relevant = {item.id for item, _ in selected + baseline}
    keys = {identifier: list(key) for identifier in sorted(relevant)
            if (key := _canonical_key((definition_keys or {}).get(identifier))) is not None}
    raw_ranks = {}
    for rank, (item, _) in enumerate(pairs, 1):
        raw_ranks.setdefault(item.id, rank)
    receipt = {
        "schema": "source_graph_allocation_v1", "mode": mode,
        "activated": bool(clauses), "clauses": list(clauses), "raw_ranks": raw_ranks,
        "canonical_keys": keys, "anchor_ids": [item.id for item, _ in anchors],
        "baseline_eligible_ids": [item.id for item, _ in baseline],
        "dependency_owners": {item.id: origins[item.id][1] for item, _ in baseline
                              if origins.get(item.id, ("retrieval", None))[0] == "dependency"},
        "graph_witnesses": [], "neighbor_keys": [], "protected_baseline_ids": [], "protected_windows": [],
        "available_candidate_ids": [], "added_ids": [], "evicted_ids": [],
        "proposed_eligible_ids": [item.id for item, _ in baseline],
        "accepted": False, "fallback_reason": None,
    }

    def finish(eligible, reason=None):
        receipt["fallback_reason"] = reason
        return selected, PlannedPairs(eligible, receipt), origins, deepcopy(receipt)

    if not clauses:
        return finish(baseline, "no_literal_clauses")
    witnesses = _graph_witnesses(anchors, references, definition_keys, ambiguous_symbols)
    neighbors = {tuple(value["neighbor_key"]) for value in witnesses}
    receipt["graph_witnesses"] = witnesses
    receipt["neighbor_keys"] = [list(key) for key in sorted(neighbors)]
    protected = set(receipt["anchor_ids"]) | set(receipt["dependency_owners"]) | set(
        receipt["dependency_owners"].values())
    for item, _ in baseline:
        if tuple(keys.get(item.id, ())) in neighbors:
            protected.add(item.id)
    receipt["protected_baseline_ids"] = [item.id for item, _ in baseline if item.id in protected]
    windows = {}
    for item, _ in baseline + selected:
        if item.id in protected or tuple(keys.get(item.id, ())) in neighbors:
            windows.setdefault(item.id, {"id": item.id, "path": item.path,
                                         "start_line": item.start_line, "end_line": item.end_line,
                                         "content_hash": item.content_hash})
    receipt["protected_windows"] = list(windows.values())
    if not witnesses:
        return finish(baseline, "no_graph_neighbors")
    exchangeable = [(item, score) for item, score in baseline if item.id not in protected]
    if not exchangeable:
        return finish(baseline, "no_exchangeable_slots")
    baseline_keys = {_definition_key(item, definition_keys) for item, _ in baseline}
    available = [(item, score) for item, score in selected
                 if _definition_key(item, definition_keys) not in baseline_keys
                 and tuple(keys.get(item.id, ())) in neighbors]
    receipt["available_candidate_ids"] = [item.id for item, _ in available]
    if not available:
        return finish(baseline, "no_new_graph_neighbor")
    additions = available[:min(MAX_ADDITIONS, len(exchangeable))]
    evicted = {item.id for item, _ in exchangeable[-len(additions):]}
    proposal = [(item, score) for item, score in baseline if item.id in protected]
    proposal += additions
    proposal += [(item, score) for item, score in baseline if item.id not in protected and item.id not in evicted]
    receipt.update(added_ids=[item.id for item, _ in additions],
                   evicted_ids=[item.id for item, _ in baseline if item.id in evicted],
                   proposed_eligible_ids=[item.id for item, _ in proposal], accepted=True)
    return finish(proposal)


def source_graph_context(pairs, **kwargs):
    return source_allocation_plan(pairs, mode="source_graph", **kwargs)[:3]


def source_graph_all_context(pairs, **kwargs):
    return source_allocation_plan(pairs, mode="source_graph_all", **kwargs)[:3]


def protected_source_rows(reference, selection_plan):
    """Protect actual delivered rows, including newly backfilled graph neighbors."""
    if selection_plan["mode"] == "source_graph_all" or not selection_plan["accepted"]:
        return list(reference["results"])
    protected = set(selection_plan["protected_baseline_ids"])
    neighbors = {tuple(key) for key in selection_plan["neighbor_keys"]}
    keys = selection_plan["canonical_keys"]
    return [row for row in reference["results"] if row["id"] in protected
            or row.get("origin") == "dependency"
            or tuple(keys.get(row["id"], ())) in neighbors
            or any(row["path"] == window["path"]
                   and row["start_line"] <= window["end_line"]
                   and window["start_line"] <= row["end_line"]
                   for window in selection_plan["protected_windows"])]


def _literal(row):
    source, start, end = row.get("source"), row.get("start_line"), row.get("end_line")
    if (not isinstance(source, str) or not source or not isinstance(row.get("path"), str)
            or type(start) is not int or type(end) is not int or start < 1):
        return None
    lines = physical_lines(source)
    if (end != start + len(lines) - 1
            or row.get("source_hash") != hashlib.sha256(source.encode("utf-8")).hexdigest()):
        return None
    return start, end, lines


def _covers(outer, inner):
    first, second = _literal(outer), _literal(inner)
    if (first is None or second is None or outer["path"] != inner["path"]
            or any((name in outer) != (name in inner) or outer.get(name) != inner.get(name)
                   for name in (*_PROVENANCE, "related_to"))
            or outer.get("origin") != inner.get("origin")):
        return False
    if outer.get("id") == inner.get("id") and (
            ("content_hash" in outer) != ("content_hash" in inner)
            or outer.get("content_hash") != inner.get("content_hash")):
        return False
    if inner.get("origin") == "dependency" and (
            outer.get("id") != inner.get("id") or outer.get("related_to") != inner.get("related_to")):
        return False
    a, b = first, second
    return (a[0] <= b[0] <= b[1] <= a[1]
            and a[2][b[0] - a[0]:b[1] - a[0] + 1] == b[2])


def _protected_rows_preserved(protected, proposal):
    delivered_ids = {row.get("id") for row in proposal["results"]}
    return all((old.get("origin") != "dependency" or old.get("related_to") in delivered_ids)
               and any(_covers(current, old) for current in proposal["results"])
               for old in protected)


def guard_source_plan(reference, proposal, selection_plan):
    """Choose from actual packed source; return its trace outside the response."""
    if (not isinstance(selection_plan, dict)
            or selection_plan.get("schema") != "source_graph_allocation_v1"
            or selection_plan.get("mode") not in MODES):
        raise ValueError("Source guard requires its actual source allocation plan")
    protected = protected_source_rows(reference, selection_plan)
    preserved = _protected_rows_preserved(protected, proposal)
    accepted = selection_plan["accepted"] and preserved
    reason = (None if accepted else "inactive_plan" if not selection_plan["accepted"]
              else "protected_source_not_preserved")
    receipt = {"schema": "source_graph_guard_v1", "mode": selection_plan["mode"],
               "protected_ids": [row["id"] for row in protected],
               "protected_rows": [{name: row[name] for name in (
                   "id", "path", "start_line", "end_line", "source_hash", "content_hash",
                   "origin", "related_to", *_PROVENANCE) if name in row}
                                  for row in protected],
               "source_preserved": preserved, "accepted": bool(accepted), "fallback_reason": reason}
    return deepcopy(proposal if accepted else reference), receipt


def guard_source_context(response, candidates, *, selection_plan, reference_factory,
                         max_results, source_budget, token_budget=None,
                         cancel_check=lambda: None, ranked_candidates=None, definition_keys=None):
    """Apply coverage backfill, then check source from one lazy control packing."""
    from semantic_search.context_backfill import coalesce_context

    if not callable(reference_factory):
        raise ValueError("Source guard requires a same-input reference factory")
    cancel_check()
    proposal = coalesce_context(response, candidates, max_results=max_results,
        source_budget=source_budget, token_budget=token_budget, cancel_check=cancel_check)
    cancel_check()
    reference = reference_factory()
    cancel_check()
    chosen, _ = guard_source_plan(reference, proposal, selection_plan)
    cancel_check()
    return chosen
