"""Research-only shared-caller protection for complementary implementations.

The original graph-neighbor planner supplies frozen source-only base facts.
Shared-flow admission protects existing context; it never retrieves a caller,
infers a receiver type, or treats connectivity as proof of query correctness.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy

from semantic_search import source_allocation as source


MODE = "shared_flow"
MAX_ADDITIONS = 2


def _canonical_graph(references, definition_keys, ambiguous_symbols):
    definitions = defaultdict(set)
    for value in (definition_keys or {}).values():
        key = source._canonical_key(value)
        if key is not None:
            definitions[key[:2]].add(key)
    ambiguous = set(ambiguous_symbols or ())
    unique = {label: next(iter(keys)) for label, keys in definitions.items()
              if len(keys) == 1 and label not in ambiguous}
    edges = getattr(references, "edges", {}) if references is not None else {}
    valid = set()
    if hasattr(edges, "items"):
        for caller, targets in edges.items():
            if not isinstance(caller, tuple) or caller not in unique:
                continue
            for target in targets:
                target = tuple(target) if isinstance(target, (tuple, list)) else None
                if target in unique and target != caller:
                    valid.add((caller, target))
    return unique, sorted(valid)


def _shared_flow(candidate, unique, edges):
    label = (candidate.path, candidate.symbol)
    if label not in unique:
        return [], [], []
    candidate_key = unique[label]
    parents = sorted({caller for caller, callee in edges if callee == label})
    flow_keys, witnesses = set(), []
    for parent in parents:
        flow_keys.add(unique[parent])
        for caller, member in edges:
            if caller != parent:
                continue
            flow_keys.add(unique[member])
            witnesses.append({"candidate_id": candidate.id, "candidate_key": list(candidate_key),
                              "orchestrator_key": list(unique[parent]), "member_key": list(unique[member])})
    return [list(unique[parent]) for parent in parents], [list(key) for key in sorted(flow_keys)], witnesses


def flow_allocation_plan(pairs, *, mode=MODE, query, max_results, include_dependencies,
                         references, definition_keys=None, ambiguous_symbols=None):
    """Admit at most two raw-rank-prefix graph neighbors with shared-flow guards.

    Stop at the first candidate whose caller/sibling protection cannot fit. A
    rejected trial never commits protection; an earlier admitted prefix remains.
    The returned ``source_plan`` is an explicit new-planner projection for the
    unchanged source guard, rather than a receipt attributed to the old planner.
    """
    if mode != MODE:
        raise ValueError("Unknown shared flow allocation mode")
    selected, base_eligible, origins, base = source.source_allocation_plan(
        pairs, mode="source_graph", query=query, max_results=max_results,
        include_dependencies=include_dependencies, references=references,
        definition_keys=definition_keys, ambiguous_symbols=ambiguous_symbols)
    by_id = {item.id: (item, score) for item, score in selected}
    # The base helper always retains original dependencies, including ones
    # outside the scored pool. Its actual eligible pairs also preserve inherited
    # owner scores when a dependency happens to have its own raw scored entry.
    original_pairs = dict(by_id)
    original_pairs.update({item.id: (item, score) for item, score in base_eligible})
    baseline = [original_pairs[identifier] for identifier in base["baseline_eligible_ids"]]
    guard_plan = deepcopy(base)
    guard_plan["producer_schema"] = "shared_flow_allocation_v1"
    guard_plan.update(added_ids=[], evicted_ids=[],
                      proposed_eligible_ids=list(base["baseline_eligible_ids"]), accepted=False)
    receipt = {"schema": "shared_flow_allocation_v1", "mode": mode,
               "base_plan": deepcopy(base), "base_protected_ids": list(base["protected_baseline_ids"]),
               "candidate_trials": [], "admitted_ids": [], "blocked_candidate_id": None,
               "flow_keys": [], "flow_witnesses": [], "termination": None,
               "accepted": False, "fallback_reason": None, "guard_plan": guard_plan}

    def finish(eligible, *, reason=None, termination=None):
        receipt["accepted"] = guard_plan["accepted"]
        receipt["fallback_reason"] = guard_plan["fallback_reason"] = reason
        receipt["termination"] = termination
        return selected, source.PlannedPairs(eligible, guard_plan), origins, deepcopy(receipt)

    if not base["accepted"]:
        return finish(baseline, reason=base["fallback_reason"], termination="inactive_base_plan")
    unique, edges = _canonical_graph(references, definition_keys, ambiguous_symbols)
    keys = base["canonical_keys"]
    protected = set(base["protected_baseline_ids"])
    flow_keys, accepted, flow_witnesses = set(), [], []
    for identifier in base["available_candidate_ids"]:
        candidate, score = by_id[identifier]
        parents, trial_keys, witnesses = _shared_flow(candidate, unique, edges)
        combined_keys = flow_keys | {tuple(key) for key in trial_keys}
        trial_protected = protected | {item.id for item, _ in baseline
                                     if tuple(keys.get(item.id, ())) in combined_keys}
        exchangeable = [item.id for item, _ in baseline if item.id not in trial_protected]
        fits = len(exchangeable) >= len(accepted) + 1
        trial = {"candidate_id": identifier, "raw_rank": base["raw_ranks"][identifier],
                 "candidate_key": keys[identifier], "orchestrator_keys": parents,
                 "flow_keys": trial_keys, "flow_witnesses": witnesses,
                 "trial_protected_baseline_ids": [item.id for item, _ in baseline if item.id in trial_protected],
                 "exchangeable_baseline_ids": exchangeable, "required_slots": len(accepted) + 1,
                 "accepted": fits, "stop_reason": None if fits else "insufficient_exchangeable_slots"}
        receipt["candidate_trials"].append(trial)
        if not fits:
            receipt["blocked_candidate_id"] = identifier
            break
        accepted.append((candidate, score))
        protected, flow_keys = trial_protected, combined_keys
        flow_witnesses.extend(witnesses)
        if len(accepted) == MAX_ADDITIONS:
            break
    if not accepted:
        return finish(baseline, reason="shared_flow_capacity_blocked", termination="capacity_blocked")
    exchangeable = [(item, score) for item, score in baseline if item.id not in protected]
    removed = {item.id for item, _ in exchangeable[-len(accepted):]}
    proposal = [(item, score) for item, score in baseline if item.id in protected]
    proposal += accepted
    proposal += [(item, score) for item, score in baseline if item.id not in protected and item.id not in removed]
    neighbor_keys = {tuple(key) for key in base["neighbor_keys"]} | flow_keys
    windows = {}
    for item, _ in baseline + selected:
        if item.id in protected or tuple(keys.get(item.id, ())) in neighbor_keys:
            windows.setdefault(item.id, {"id": item.id, "path": item.path,
                "start_line": item.start_line, "end_line": item.end_line, "content_hash": item.content_hash})
    guard_plan.update(neighbor_keys=[list(key) for key in sorted(neighbor_keys)],
        protected_baseline_ids=[item.id for item, _ in baseline if item.id in protected],
        protected_windows=list(windows.values()), added_ids=[item.id for item, _ in accepted],
        evicted_ids=[item.id for item, _ in baseline if item.id in removed],
        proposed_eligible_ids=[item.id for item, _ in proposal], accepted=True)
    receipt.update(admitted_ids=[item.id for item, _ in accepted],
                   flow_keys=[list(key) for key in sorted(flow_keys)], flow_witnesses=flow_witnesses)
    termination = ("capacity_blocked" if receipt["blocked_candidate_id"] is not None
                   else "max_additions" if len(accepted) == MAX_ADDITIONS else "candidate_pool_exhausted")
    return finish(proposal, termination=termination)


def shared_flow_context(pairs, **kwargs):
    return flow_allocation_plan(pairs, **kwargs)[:3]
