"""Immutable observed data for complementary-context research; no producers/models.

The shared trust boundary is explicit: this module reads previously audited
inventories, raw ranks, static edges and original controls. It does not select,
pack, score evidence or import semantic_search. New auditors reconstruct those
operations independently. Historical sources are checked as Git blobs, never
attributed to a later checkout.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import dataclass, asdict
import gzip
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
WINDOWS = "results/window-development-2026-10-21"
GRAPH = "results/source-allocation-development-2026-10-27"
FLOW = "results/flow-development-2026-10-29"
NATIVE = "results/flow-native-holdout-2026-10-29"
ASPECT = "results/allocation-development-2026-10-26"
CONTROLS = ("legacy", "coverage_control")
FIELDS = ("query", "scope", "match_status", "results", "total_source_chars",
          "budget_exhausted", "token_usage")


def require(value, message):
    if not value:
        raise ValueError(message)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                      separators=(",", ":")).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def file_sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read(path):
    return json.loads(Path(path).read_bytes())


def git_sha(root, commit, name):
    return sha(subprocess.check_output(["git", "-C", str(root), "show", commit + ":" + name]))


def rows(path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            yield number, json.loads(line)


def source_view(response):
    require(all(field in response for field in FIELDS), "Missing source-context field")
    result = {field: deepcopy(response[field]) for field in FIELDS}
    if "_selection_backfill" in response:
        result["_selection_backfill"] = deepcopy(response["_selection_backfill"])
    return result


@dataclass(frozen=True)
class Window:
    id: str
    path: str
    symbol: str
    start_line: int
    end_line: int
    text: str
    content_hash: str


class FrozenGraph:
    """Literal previously audited target order and earliest-window lookup."""
    def __init__(self, inventory, edges):
        self.edges = {tuple(row["caller"]): [tuple(target) for target in row["targets"]]
                      for row in edges}
        self.windows = defaultdict(list)
        for item in inventory.values():
            self.windows[item.path, item.symbol].append(item)
        for values in self.windows.values():
            values.sort(key=lambda item: (item.start_line, item.id))

    def dependencies(self, item, limit=1):
        require(type(limit) is int and limit >= 0, "Invalid dependency limit")
        output = []
        for target in self.edges.get((item.path, item.symbol), ()):
            if len(output) >= limit:
                break
            if self.windows.get(target):
                output.append(self.windows[target][0])
        return output


def reference(path, number, row):
    return {"path": path, "line_number": number, "record_sha256": sha(canonical(row)),
            "response_sha256": sha(canonical(row["response"])),
            "compact_sha256": row["compact_sha256"]}


def historical_study(root, folder):
    """Validate immutable audited data and original execution/freeze blobs."""
    audit = read(root / folder / "independent-audit.json")
    require(audit.get("status") == "passed" and audit.get("checks_passed") is True,
            "Successful historical audit required: " + folder)
    artifacts = {folder + "/" + name: digest for name, digest in audit["artifact_sha256"].items()}
    artifacts[folder + "/independent-audit.json"] = file_sha(root / folder / "independent-audit.json")
    for name, digest in artifacts.items():
        require(file_sha(root / name) == git_sha(root, "HEAD", name) == digest,
                "Historical data is changed or uncommitted: " + name)
    native = (root / folder / "plan.json").exists()
    plan_name = "plan.json" if native else "frozen-plan.json"
    start_name = "run-started.json" if native else "run-start.json"
    plan, start = read(root / folder / plan_name), read(root / folder / start_name)
    # The first window replay predates the explicit start-file freeze field;
    # its original independent audit resolves that exact committed freeze.
    require(start["execution_commit"] == audit["producer_execution"]
            and start.get("prospective_freeze", audit["producer_freeze"]) == audit["producer_freeze"]
            and start["plan_sha256"] == file_sha(root / folder / plan_name),
            "Original historical identity differs: " + folder)
    frozen = audit["producer_freeze"]["commit"]
    execution = start["execution_commit"]
    subprocess.check_call(["git", "-C", str(root), "merge-base", "--is-ancestor", frozen, execution])
    bound = dict(plan["binding"]["implementation_sha256"] if native else plan["producer_sha256"])
    bound.update(plan.get("input_sha256", {}))
    binding = plan.get("binding", {})
    for parent in ("historical_parent", "negative_parent", "source_parent"):
        bound.update(plan.get(parent, {}).get("artifacts", {}))
    bound.update(binding.get("development_parent", {}).get("artifacts", {}))
    bound.update(binding.get("definition_preflight") or {})
    bound.update({folder + "/" + name: digest for name, digest in plan.get("artifacts", {}).items()})
    bound[folder + "/" + plan_name] = file_sha(root / folder / plan_name)
    for name in ("baseline-gate.json", "prior-coverage-gate.json"):
        if (root / folder / name).exists():
            bound[folder + "/" + name] = file_sha(root / folder / name)
    require(len(bound) == audit["producer_freeze"]["bound_blob_count"],
            "Complete original prospective binding count differs: " + folder)
    for name, digest in bound.items():
        require(git_sha(root, frozen, name) == git_sha(root, execution, name) == digest,
                "Original historical source blob differs: " + name)
    auditor = audit["auditor_execution_commit"]
    for name, digest in audit["auditor_sha256"].items():
        require(git_sha(root, auditor, name) == digest, "Original auditor blob differs: " + name)
    ancestor = subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor", execution, "HEAD"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
    require(ancestor in (0, 1), "Cannot resolve original history")
    return {"artifacts": artifacts, "original_freeze": audit["producer_freeze"],
            "original_execution": execution, "original_auditor": auditor,
            "original_execution_is_current_ancestor": ancestor == 0,
            "source_binding_count": len(bound),
            "history_note": "Original blobs and identities retained; rewritten old window history is data, not current source execution."}


def fixture(value, graph):
    inventory = {row["id"]: Window(**row) for row in value["inventory"]}
    require(len(inventory) == len(value["inventory"]), "Duplicate frozen source window")
    keys = {name: tuple(key) for name, key in value["definition_keys"].items()}
    labels = defaultdict(set)
    for key in keys.values():
        labels[key[:2]].add(key)
    return {"inventory": inventory, "definition_keys": keys,
            "graph": FrozenGraph(inventory, graph),
            "ambiguous_symbols": {label for label, definitions in labels.items() if len(definitions) > 1}}


def case_key(row):
    return tuple(row[name] for name in ("subset", "dataset", "query_id", "condition"))


def make_case(row, fx, *, qualified=False):
    pairs = []
    for pair in row["stages"]["ranked"]:
        item = fx["inventory"].get(pair["snippet"]["id"])
        require(item is not None and asdict(item) == pair["snippet"], "Raw source window differs from frozen inventory")
        score = pair["score"]
        require(type(score) in (int, float) and __import__("math").isfinite(score), "Nonfinite original scalar")
        pairs.append((item, score))
    require(len(pairs) <= 50 and len({item.id for item, _ in pairs}) == len(pairs), "Original raw candidate cap/identity differs")
    return {**{name: row[name] for name in ("subset", "dataset", "query_id", "condition", "kind", "multi_file")},
            "groups": deepcopy(row["evidence_groups"]), "qualified": qualified,
            "pairs": pairs, "original": source_view(row["response"]), **fx}


def load(root=ROOT, *, verify_history=True):
    """393 observed cases/201 units; historical negative outcomes are data only."""
    root = Path(root)
    parents = {folder: historical_study(root, folder) for folder in (WINDOWS, ASPECT, GRAPH, FLOW, NATIVE)} if verify_history else {}
    raw_fixtures = read(root / WINDOWS / "source-fixtures.json")["fixtures"]
    graphs = read(root / GRAPH / "graph-fixtures.json")["fixtures"]
    fixtures = {}
    for signature, value in raw_fixtures.items():
        require(signature in graphs and value["source_sha256"] == graphs[signature]["source_sha256"], "Frozen graph/source mismatch")
        require(value["dataset"] not in fixtures, "Ambiguous frozen dataset snapshot")
        fixtures[value["dataset"]] = fixture(value, graphs[signature]["edges"])
    cases, controls, negatives, control_inputs = {}, {}, [], {}
    def matching_control_input(key, row, groups):
        fingerprint = {"ranked": row["stages"]["ranked"], "query": row["response"]["query"],
            "scope": row["response"]["scope"], "kind": row["kind"], "multi_file": row["multi_file"], "groups": groups}
        require(key not in control_inputs or control_inputs[key] == fingerprint,
                "Original controls used different ranked/source/query/label inputs")
        control_inputs[key] = fingerprint
    old_path = WINDOWS + "/replay.jsonl.gz"
    for number, row in rows(root / old_path):
        require(sha(row["compact_payload"].encode()) == row["compact_sha256"], "Original compact digest differs")
        key = case_key(row)
        if row["selector"] in CONTROLS:
            matching_control_input(key, row, row["evidence_groups"])
            require((row["selector"], key) not in controls, "Duplicate historical control")
            controls[row["selector"], key] = {"response": source_view(row["response"]), "reference": reference(old_path, number, row)}
            if row["selector"] == "legacy":
                cases[key] = make_case(row, fixtures[row["dataset"]])
        else:
            negatives.append({"condition": row["selector"], **reference(old_path, number, row)})
    require(len(cases) == 365 and len(controls) == 730, "Complete original365-case/two-control matrix required")
    for folder in (ASPECT, GRAPH, FLOW):
        path = folder + "/replay.jsonl.gz"
        for number, row in rows(root / path):
            negatives.append({"condition": row["selector"], **reference(path, number, row)})
    native_fixture = read(root / NATIVE / "source-inventory.json")
    nfx = fixture(native_fixture, native_fixture["graph_edges"])
    native_binding = read(root / NATIVE / "plan.json")["binding"]
    label_path = native_binding["dataset_path"]
    require(file_sha(root / label_path) == native_binding["dataset_sha256"]
            and (not verify_history or git_sha(root, "HEAD", label_path) == native_binding["dataset_sha256"]),
            "Observed TanStack gold changed from its immutable native source plan")
    labels = read(root / label_path)
    queries = {query["id"]: query for query in labels["queries"]}
    native_path = NATIVE + "/searches.jsonl.gz"
    for number, row in rows(root / native_path):
        if row["repetition"] != 1:
            continue
        identity = {"subset": "tanstack_observed", "dataset": "tanstack", "query_id": row["query_id"], "condition": row["retrieval"]}
        key = case_key(identity)
        if row["arm"] in CONTROLS:
            query = queries[row["query_id"]]
            groups = query.get("groups", query.get("target_groups", [query.get("targets")]))
            if query["kind"] == "absent":
                groups = []
            is_multi = len({target["path"] for alternatives in groups for target in alternatives}) > 1
            require(row["response"]["query"] == query["query"] and row["kind"] == query["kind"]
                    and row["multi_file"] is is_multi, "Observed native query/label identity differs")
            matching_control_input(key, row, groups)
            if row["arm"] == "legacy":
                shaped = {**row, **identity, "evidence_groups": groups}
                cases[key] = make_case(shaped, nfx, qualified=True)
            controls[row["arm"], key] = {"response": source_view(row["response"]), "reference": reference(native_path, number, row)}
        else:
            negatives.append({"condition": row["condition"], **reference(native_path, number, row)})
    require(len(cases) == 393 and len(controls) == 786 and len(negatives) == 2948,
            "Exact393cases/786controlrefs/2948negative refs required")
    require(len({(case["dataset"], case["query_id"]) for case in cases.values()}) == 201, "Exactly201 distinct observed units required")
    require(set(controls) == {(name, key) for name in CONTROLS for key in cases}, "Incomplete control matrix")
    return list(cases.values()), controls, negatives, parents


def counts(cases):
    return {"observed_rows": len(cases), "distinct_dataset_query_units": len({(c["dataset"], c["query_id"]) for c in cases}),
            "rows_by_subset": dict(Counter(c["subset"] for c in cases)),
            "warning": "Shared queries/modes are repeated units, not independent trials."}
