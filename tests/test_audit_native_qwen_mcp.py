"""Independent smoke controls, exercised only with CPU/stub file witnesses."""

import ast
import base64
import copy
import gzip
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
STUDY = ROOT / "results/qwen-native-mcp-smoke-2026-10-14"
SPEC = importlib.util.spec_from_file_location(
    "native_qwen_mcp_independent_audit", ROOT / "scripts/audit_native_qwen_mcp.py"
)
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


class CPUTokenizer:
    def encode(self, text, *, add_special_tokens):
        assert add_special_tokens is False
        return SimpleNamespace(ids=[ord(char) for char in text])


def literal_core():
    values = audit.assignments(ROOT / "src/semantic_search/qwen_reranker_core.py")
    return {name: ast.literal_eval(values[name]) for name in audit.CORE_CONSTANTS}


def literal_fixture():
    values = audit.assignments(ROOT / "scripts/smoke_qwen_mcp.py")
    source = ast.literal_eval(values["SOURCE"].left) + "".join(
        f"\ndef format_noise_{index}(value):\n    return str(value) + '-{index}'\n"
        for index in range(58)
    )
    warmup = ast.literal_eval(
        audit.assignments(ROOT / "src/semantic_search/qwen_worker.py")["WARMUP"]
    )
    return source, source.replace("attempt, 60", "attempt, 90"), literal_core(), warmup


@pytest.fixture
def observed(tmp_path, monkeypatch):
    # Deliberately synthetic 256-byte ranks: CPU fixtures do not download or
    # depend on an untracked production tokenizer cache on Linux CI.
    raw = b"".join(
        base64.b64encode(bytes([value])) + b" " + str(value).encode() + b"\n"
        for value in range(256)
    )
    ranks = tmp_path / "cpu-byte-ranks"
    ranks.write_bytes(raw)
    monkeypatch.setattr(audit, "RANKS_PATH", ranks)
    monkeypatch.setattr(audit, "RANKS_SHA", audit.digest(raw))
    monkeypatch.setattr(audit, "RANKS_COUNT", 256)
    plan = audit.read(STUDY / "frozen-plan.json")
    result = audit.read(STUDY / "results.json")
    ledger = [
        audit.decode(line)
        for line in (STUDY / "native-forwards.jsonl").read_text().splitlines()
    ]
    source, edited, core, warmup = literal_fixture()
    native = {row["request_id"]: [] for row in ledger}
    for row in ledger:
        native[row["request_id"]].append(row)
    tokenizer = CPUTokenizer()

    def replace_input(query, text, receipt, request):
        ids = (
            tokenizer.encode(core["PREFIX"], add_special_tokens=False).ids
            + tokenizer.encode(
                f"<Instruct>: {core['INSTRUCTION']}\n<Query>: {query}\n<Document>: "
                + text,
                add_special_tokens=False,
            ).ids
            + tokenizer.encode(core["SUFFIX"], add_special_tokens=False).ids
        )
        receipt["total_tokens"] = len(ids)
        for event in native[request]:
            event["total_tokens"] = len(ids)
            event["input_ids_sha256"] = audit.digest(
                json.dumps(ids, separators=(",", ":")).encode()
            )

    replace_input(
        warmup["query"],
        warmup["source"],
        result["owner"]["ready"]["warmup"]["result"],
        "generic-warmup",
    )
    for index, step in enumerate(result["steps"]):
        if step["phase"] in {"watcher_saved_edit", "mcp_explicit_disable"}:
            continue
        observation = result["observations"][index + 1 if index < 2 else index]
        text = source if step["phase"].endswith("before") else edited
        lines = audit.physical_lines(text)
        spans = {
            f"src/sample.py:{node.name}:{node.lineno}-{node.end_lineno}": "".join(
                lines[node.lineno - 1 : node.end_lineno]
            )
            for node in ast.parse(text).body
            if isinstance(node, ast.FunctionDef)
        }
        for candidate, receipt in observation["response"]["reranker_receipts"].items():
            replace_input(
                plan["fixture"]["query"],
                spans[candidate],
                receipt["result"],
                receipt["request_id"],
            )
        if "response" in step:
            step["response"] = copy.deepcopy(observation["response"])
    for index, step in enumerate(result["steps"]):
        if step["phase"] == "watcher_saved_edit":
            continue
        observation = result["observations"][index + 1 if index < 2 else index]
        recompute_compact_tokens(observation["response"])
        if "response" in step:
            step["response"] = copy.deepcopy(observation["response"])
        else:
            recompute_compact_tokens(step["context"])
    return plan, result, ledger, source, edited, tokenizer, core, warmup


def check(observed):
    plan, result, ledger, source, edited, tokenizer, core, warmup = observed
    return audit.validate(plan, result, ledger, source, edited, tokenizer, core, warmup)


def test_all_97_cpu_inputs_reconcile_and_remain_toy_evidence(observed):
    metrics = check(observed)
    assert metrics["native_forwards"] == metrics["reconstructed_input_pairs"] == 97
    assert metrics["measured_forwards"] == 96 and metrics["warmup_forwards"] == 1
    assert metrics["compact_tokens"] == {
        "http_before": 2719,
        "mcp_before": 2719,
        "http_after": 2719,
        "mcp_after": 2719,
        "mcp_explicit_disable": 2712,
    }
    assert (
        metrics["saved_edit_indexed_windows"] == 1
        and metrics["saved_edit_reused_windows"] == 60
    )


@pytest.mark.parametrize(
    "mutation,reason",
    [
        ("native-event-missing", "event inventory"),
        ("native-order", "event alternation"),
        ("native-start-input", "event identity"),
        ("native-bool-batch", "singleton native"),
        ("native-extra-field", "event fields"),
        ("native-double-warmup", "singleton native"),
        ("native-duplicate-request", "Unique native"),
        ("input-ids", "tokenizer input IDs"),
        ("native-score", "scalar receipt"),
        ("native-bool-counter", "singleton native"),
        ("input-bool-token", "input counters"),
        ("source-bytes", "Literal source bytes"),
        ("truncated-source", "Unclipped"),
        ("extra-result-field", "13-field"),
        ("warmup-reclassified", "warmup identity"),
        ("wrong-prompt", "input identity"),
        ("token-count", "compact token count"),
        ("saved-span", "token count"),
        ("bool-span", "Typed saved source span"),
        ("source-exclusion", "token count"),
        ("mcp-output-changed", "Compact query/status"),
        ("http-output-changed", "output equality"),
        ("mcp-override", "Query-only"),
        ("mcp-effective-override", "effective HTTP"),
        ("schema-default", "schema default"),
        ("bool-shortlist", "defaults"),
        ("full-pool", "prefix/full50"),
        ("unscored-prefix", "singleton receipts"),
        ("phase-extra-score", "native after counts"),
        ("disabled-score", "adds no scores"),
        ("saved-edit-full-index", "window accounting"),
        ("saved-edit-rerun", "refresh"),
        ("saved-edit-search-trigger", "GET-status-only"),
        ("embedding-extra", "embedding entries"),
        ("forced-kill", "Clean owned stop"),
        ("bool-exit", "Clean owned stop"),
        ("auth-present", "Auth/listener"),
        ("listener-open", "Auth/listener"),
        ("bool-final-counter", "Final stop native counts"),
        ("cached-scoring", "no-cache"),
        ("source-changed", "source bindings stable"),
        ("interrupted", "Completed native"),
        ("extra-mcp-envelope", "envelope inventory"),
        ("extra-score-receipt", "score receipt fields"),
    ],
)
def test_mutated_actual_receipts_cannot_qualify(observed, mutation, reason):
    plan, result, ledger, source, edited, tokenizer, core, warmup = observed
    receipt = next(
        iter(result["observations"][1]["response"]["reranker_receipts"].values())
    )
    if mutation == "native-event-missing":
        ledger.pop()
    elif mutation == "native-order":
        ledger[0], ledger[1] = ledger[1], ledger[0]
    elif mutation == "native-start-input":
        ledger[0]["input_ids_sha256"] = "0" * 64
    elif mutation == "native-bool-batch":
        ledger[0]["batch_size"] = ledger[1]["batch_size"] = True
    elif mutation == "native-extra-field":
        ledger[0]["padding"] = 0
    elif mutation == "native-double-warmup":
        ledger[2]["warmup"] = ledger[3]["warmup"] = True
    elif mutation == "native-duplicate-request":
        ledger[2]["request_id"] = ledger[3]["request_id"] = ledger[0]["request_id"]
    elif mutation == "input-ids":
        ledger[0]["input_ids_sha256"] = ledger[1]["input_ids_sha256"] = "0" * 64
    elif mutation == "native-score":
        ledger[1]["score"] = {
            "yes_logit": 0.0,
            "no_logit": 0.0,
            "logit_difference": 0.0,
            "yes_rank_signal": 0.5,
        }
    elif mutation == "native-bool-counter":
        ledger[0]["forward_number"] = ledger[1]["forward_number"] = True
    elif mutation == "input-bool-token":
        result["owner"]["ready"]["warmup"]["result"]["total_tokens"] = True
    elif mutation == "source-bytes":
        result["owner"]["ready"]["warmup"]["result"]["source_utf8_bytes"] += 1
    elif mutation == "truncated-source":
        result["owner"]["ready"]["warmup"]["result"]["truncated"] = True
    elif mutation == "extra-result-field":
        result["owner"]["ready"]["warmup"]["result"]["cached"] = False
    elif mutation == "warmup-reclassified":
        result["owner"]["ready"]["warmup"]["warmup"] = False
    elif mutation == "wrong-prompt":
        core["INSTRUCTION"] += " Changed."
    elif mutation == "token-count":
        result["steps"][1]["context"]["token_usage"]["tokens"] += 1
    elif mutation == "saved-span":
        result["steps"][1]["context"]["results"][0]["source"] += "changed"
    elif mutation == "bool-span":
        for context in (
            result["steps"][1]["context"],
            result["observations"][2]["response"],
        ):
            context["results"][0]["start_line"] = True
        recompute_compact_tokens(result["steps"][1]["context"])
        result["observations"][2]["response"]["token_usage"] = copy.deepcopy(
            result["steps"][1]["context"]["token_usage"]
        )
    elif mutation == "source-exclusion":
        result["steps"][1]["context"]["scope"]["source_exclusions"] = ["src/sample.py"]
    elif mutation == "mcp-output-changed":
        result["steps"][1]["context"]["query"] += "changed"
    elif mutation == "http-output-changed":
        result["observations"][1]["response"]["results"][0]["source"] += "changed"
    elif mutation == "mcp-override":
        result["steps"][1]["raw_tool_arguments"]["candidates"] = 12
    elif mutation == "mcp-effective-override":
        result["steps"][1]["effective_http_settings"]["candidates"] = 12
    elif mutation == "schema-default":
        result["mcp"]["schema"]["properties"]["rerank"]["default"] = False
    elif mutation == "bool-shortlist":
        plan["defaults"]["candidates"] = True
    elif mutation == "full-pool":
        result["observations"][2]["response"]["retrieval_candidate_windows"] = 24
    elif mutation == "unscored-prefix":
        result["observations"][2]["response"]["reranker_receipts"].popitem()
    elif mutation == "phase-extra-score":
        result["steps"][-1]["native_after"]["requests"] += 1
    elif mutation == "disabled-score":
        result["observations"][-1]["response"]["reranker_receipts"] = {
            "reused": receipt
        }
    elif mutation == "saved-edit-full-index":
        result["steps"][2]["status"]["indexstats"]["indexed_windows"] = 61
    elif mutation == "saved-edit-rerun":
        result["steps"][2]["status"]["watcher"]["refresh_count"] = 2
    elif mutation == "saved-edit-search-trigger":
        result["steps"][2]["trigger_requests"] = "POST /search"
    elif mutation == "embedding-extra":
        result["embedding_counts"]["query_calls"] += 1
    elif mutation == "forced-kill":
        result["owner"]["stop"]["forced_kill"] = True
    elif mutation == "bool-exit":
        result["owner"]["stop"]["exit_code"] = False
    elif mutation == "auth-present":
        result["owner"]["worker_stop"]["auth_token_removed"] = False
    elif mutation == "listener-open":
        result["owner"]["worker_stop"]["listener_closed"] = False
    elif mutation == "bool-final-counter":
        result["native_final_status"]["counts"]["requests"] = True
    elif mutation == "cached-scoring":
        result["owner"]["score_caching"] = True
    elif mutation == "source-changed":
        result["source_sha256"]["scripts/smoke_qwen_mcp.py"] = "0" * 64
    elif mutation == "interrupted":
        result["interrupt_signals"] = {"SIGTERM": 1}
    elif mutation == "extra-mcp-envelope":
        result["steps"][1]["context"]["unaccounted_context"] = "extra payload"
    elif mutation == "extra-score-receipt":
        next(iter(result["observations"][2]["response"]["reranker_receipts"].values()))[
            "cached"
        ] = True
    else:
        raise AssertionError(mutation)
    with pytest.raises(ValueError, match=reason):
        check(observed)


def recompute_compact_tokens(context):
    encoder = audit.offline_encoding()
    for _ in range(5):
        context["token_usage"]["tokens"] = len(
            encoder.encode(
                json.dumps(
                    audit.compact(context), ensure_ascii=False, separators=(",", ":")
                ),
                disallowed_special=(),
            )
        )


def test_artifact_inventory_and_clock_bindings_on_retained_public_fixture():
    plan, certificate, result, ledger = audit.artifacts(STUDY)
    assert len(ledger) == 194 and certificate["cpu_injected"] is False
    assert plan["cpu_injected"] is False and result["native_inference"] is True


@pytest.mark.parametrize("mode", ["duplicate", "nan", "inf"])
def test_ambiguous_or_nonfinite_json_rejected(mode):
    raw = {"duplicate": '{"x":1,"x":2}', "nan": '{"x":NaN}', "inf": '{"x":Infinity}'}[
        mode
    ]
    with pytest.raises(ValueError):
        audit.decode(raw)


@pytest.mark.parametrize("change", ["early-stop", "naive", "non-utc"])
def test_frozen_start_cleanup_clock_order_is_strict(change):
    plan, certificate, result, _ = audit.artifacts(STUDY)
    start = audit.read(STUDY / "run-start.json")
    if change == "early-stop":
        result["owner"]["worker_stop"]["stopped_at_utc"] = plan["frozen_at_utc"]
    elif change == "naive":
        start["started_at_utc"] = "2026-10-14T07:27:32"
    else:
        start["started_at_utc"] = "2026-10-14T03:27:32-04:00"
    with pytest.raises(ValueError, match="clock"):
        audit.clock_order(plan, start, certificate, result["owner"])


@pytest.mark.parametrize(
    "path", ["relative/path", "<UNKNOWN>/path", "<WORK>/../outside", "/tmp/../outside"]
)
def test_unknown_relative_or_traversal_path_cannot_bind(path, tmp_path):
    with pytest.raises(ValueError, match="path"):
        audit.local_path(path, tmp_path / "root", tmp_path / "work")


def test_work_project_home_paths_expand_and_publication_match(tmp_path):
    root, work = tmp_path / "root", tmp_path / "root/.cache/alternate-work"
    assert (
        audit.local_path("<WORK>/worker-private", root, work) == work / "worker-private"
    )
    assert audit.local_path("<PROJECT>/src", root, work) == root / "src"
    assert audit.public({"x": str(work / "worker-private")}, root, work) == {
        "x": "<WORK>/worker-private"
    }


@pytest.fixture
def alternate_runtime(tmp_path, monkeypatch):
    """No model/native package bytes: explicit metadata and stat witnesses only."""
    root = tmp_path / "project"
    work = root / ".cache/fresh-smoke"
    venv = root / ".cache/fresh-reranker/venv"
    python = venv / "bin/python"
    python.parent.mkdir(parents=True)
    python.write_text("CPU interpreter placeholder")
    (venv / "pyvenv.cfg").write_text(
        "include-system-site-packages = false\nversion_info = 3.12\n"
    )
    lock = root / "requirements-qwen-reranker.lock"
    lock.write_bytes((ROOT / lock.name).read_bytes())
    site = venv / "lib/python3.12/site-packages"
    site.mkdir(parents=True)
    for line in lock.read_text().splitlines():
        name, version = line.split("==")
        metadata = site / f"{name}-{version}.dist-info/METADATA"
        metadata.parent.mkdir()
        metadata.write_text(
            f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n"
        )
    qwen = site / "mlx_lm/models/qwen3.py"
    qwen.parent.mkdir(parents=True)
    checkpoint = root / ".cache/fresh-checkpoint"
    checkpoint.mkdir()
    plan = audit.read(STUDY / "frozen-plan.json")
    result = audit.read(STUDY / "results.json")
    core = literal_core()
    old_runtime = "<PROJECT>/.cache/reranker-experiment/.venv"
    old_model = "<PROJECT>/.cache/models/qwen3-reranker-0.6b-original"

    def convert(value):
        if isinstance(value, dict):
            return {key: convert(item) for key, item in value.items()}
        if isinstance(value, list):
            return [convert(item) for item in value]
        if isinstance(value, str):
            return value.replace(old_runtime, str(venv)).replace(
                old_model, str(checkpoint)
            )
        return value

    result = convert(result)
    result["owner"]["interpreter"] = {
        "invocation_path": str(python),
        "resolved_binary": str(python),
    }
    identities = result["owner"]["ready"]["provenance"]
    fake_hashes = {qwen: identities["runtime"]["qwen3_source_sha256"]}
    for name, expected in identities["runtime"]["supporting_source_sha256"].items():
        fake_hashes[qwen.with_name(name)] = expected
    for name, (_, expected) in core["PINS"].items():
        if name != "model.safetensors":
            fake_hashes[checkpoint / name] = expected
    for path in fake_hashes:
        path.write_text("CPU-only file witness placeholder")
    (checkpoint / "model.safetensors").write_text(
        "Not a model; no weight bytes are read"
    )
    original_hash = audit.file_digest

    def stub_hash(path):
        if path.name == "model.safetensors":
            raise AssertionError("Audit must never reread model weights")
        return fake_hashes.get(path) or original_hash(path)

    monkeypatch.setattr(audit, "file_digest", stub_hash)
    monkeypatch.setattr(
        audit,
        "witness",
        lambda path: identities["model"]["file_witnesses"][path.name][1:],
    )
    monkeypatch.setattr(
        audit,
        "python_metadata",
        lambda path: {
            "python": "3.12.15",
            "executable": str(path),
            "isolated": 1,
            "no_site": 1,
        },
    )
    monkeypatch.setattr(
        audit, "git_bytes", lambda root_, revision, path: lock.read_bytes()
    )
    monkeypatch.setattr(
        audit.importlib.metadata,
        "version",
        lambda name: plan["runtime"]["main_packages"][name],
    )
    return root, work, python, checkpoint, plan, result, core


def test_alternate_explicit_runtime_and_checkpoint_paths_with_cpu_witnesses(
    alternate_runtime,
):
    proof = audit.runtime_proof(*alternate_runtime)
    assert proof["runtime_package_count"] == 34
    assert proof["weight_bytes_read_in_audit"] is False
    assert proof["checkpoint_physical_witnesses_unchanged"] is True


@pytest.mark.parametrize(
    "change,reason",
    [
        ("wrong-interpreter", "interpreter binding"),
        ("wrong-checkpoint", "checkpoint identity"),
        ("wrong-import-origin", "provenance stable"),
        ("wrong-package", "package closure"),
        ("extra-package", "package closure"),
        ("bool-witness", "physical witness"),
        ("changed-witness", "unchanged after"),
        ("unverified-weight", "checkpoint identity"),
        ("wrong-pinned-weight", "Pinned measured checkpoint SHA256"),
        ("python-patch", "Python metadata"),
        ("site-enabled", "Python metadata"),
        ("system-site", "configuration"),
    ],
)
def test_alternate_paths_cannot_loosen_native_identity(
    alternate_runtime, monkeypatch, change, reason
):
    root, work, python, checkpoint, plan, result, core = alternate_runtime
    if change == "wrong-interpreter":
        python = python.parent / "other-python"
    elif change == "wrong-checkpoint":
        checkpoint = checkpoint.with_name("wrong-checkpoint")
    elif change == "wrong-import-origin":
        result["owner"]["ready"]["provenance"]["runtime"][
            "imported_qwen3_source_path"
        ] += ".wrong"
    elif change == "wrong-package":
        path = (
            python.parent.parent
            / "lib/python3.12/site-packages/mlx-0.32.3.dist-info/METADATA"
        )
        path.write_text("Metadata-Version: 2.1\nName: mlx\nVersion: 0.0.0\n")
    elif change == "extra-package":
        path = (
            python.parent.parent
            / "lib/python3.12/site-packages/unexpected.dist-info/METADATA"
        )
        path.parent.mkdir()
        path.write_text("Metadata-Version: 2.1\nName: unexpected\nVersion: 1\n")
    elif change == "bool-witness":
        for status in (
            result["owner"]["ready"],
            result["owner"]["start_status"],
            result["owner"]["latest_status"],
            result["native_final_status"],
        ):
            status["provenance"]["model"]["file_witnesses"]["model.safetensors"][1] = (
                True
            )
    elif change == "changed-witness":
        monkeypatch.setattr(audit, "witness", lambda path: [0, 0, 0, 0, 0])
    elif change in {"unverified-weight", "wrong-pinned-weight"}:
        for status in (
            result["owner"]["ready"],
            result["owner"]["start_status"],
            result["owner"]["latest_status"],
            result["native_final_status"],
        ):
            if change == "unverified-weight":
                status["provenance"]["model"]["weight_identity_verified"] = False
            else:
                status["provenance"]["model"]["files"]["model.safetensors"][
                    "sha256"
                ] = "0" * 64
    elif change == "python-patch":
        monkeypatch.setattr(
            audit,
            "python_metadata",
            lambda path: {
                "python": "3.12.14",
                "executable": str(path),
                "isolated": 1,
                "no_site": 1,
            },
        )
    elif change == "site-enabled":
        monkeypatch.setattr(
            audit,
            "python_metadata",
            lambda path: {
                "python": "3.12.15",
                "executable": str(path),
                "isolated": 1,
                "no_site": 0,
            },
        )
    elif change == "system-site":
        (python.parent.parent / "pyvenv.cfg").write_text(
            "include-system-site-packages = true\n"
        )
    else:
        raise AssertionError(change)
    with pytest.raises(ValueError, match=reason):
        audit.runtime_proof(root, work, python, checkpoint, plan, result, core)


def test_stdlib_python_metadata_disables_site_and_pth_startup(monkeypatch, tmp_path):
    calls = []

    def subprocess_stub(command, **kwargs):
        calls.append(command)
        return (
            '{"python":"3.12.15","executable":"/fake/python","isolated":1,"no_site":1}'
        )

    monkeypatch.setattr(audit.subprocess, "check_output", subprocess_stub)
    assert audit.python_metadata(tmp_path / "python")["no_site"] == 1
    assert calls[0][1:5] == ["-I", "-S", "-B", "-c"]
    assert "import json,platform,sys" in calls[0][-1]
    assert "semantic_search" not in calls[0][-1] and "mlx" not in calls[0][-1]


def test_failed_audit_is_retained_and_cannot_overwrite(monkeypatch, tmp_path):
    destination = tmp_path / "audit.json"

    def failing(*args, **kwargs):
        raise ValueError("wrong frozen binding")

    monkeypatch.setattr(audit, "audit", failing)
    args = [
        "--study",
        str(tmp_path / "study"),
        "--work-dir",
        str(tmp_path / "work"),
        "--reranker-python",
        str(tmp_path / "python"),
        "--checkpoint",
        str(tmp_path / "checkpoint"),
        "--output",
        str(destination),
    ]
    assert audit.main(args) == 1
    evidence = audit.read(destination)
    assert (
        evidence["checks_passed"] is False
        and evidence["error"] == "wrong frozen binding"
    )
    assert (
        evidence["native_calls"] == 0
        and evidence["weight_bytes_read_in_audit"] is False
    )
    raw = destination.read_bytes()
    with pytest.raises(ValueError, match="no overwrite"):
        audit.main(args)
    assert destination.read_bytes() == raw


def test_no_native_or_producer_import_is_part_of_auditor_source():
    tree = ast.parse((ROOT / "scripts/audit_native_qwen_mcp.py").read_text())
    imports = [
        node.module if isinstance(node, ast.ImportFrom) else alias.name
        for node in ast.walk(tree)
        for alias in (
            [SimpleNamespace(name=node.module)]
            if isinstance(node, ast.ImportFrom)
            else node.names
            if isinstance(node, ast.Import)
            else []
        )
    ]
    assert not any(
        name.startswith(("semantic_search", "mlx", "smoke_qwen_mcp"))
        for name in imports
    )


@pytest.fixture
def private_witnesses(tmp_path, monkeypatch):
    root = tmp_path / "alternate-project"
    work = root / ".cache/alternate-smoke"
    study = root / "results/smoke"
    study.mkdir(parents=True)
    plan = audit.read(STUDY / "frozen-plan.json")
    result = audit.read(STUDY / "results.json")
    private = work / "worker-private/owned-cpu-witness"
    private.mkdir(parents=True, mode=0o700)

    def local(value):
        if isinstance(value, dict):
            return {key: local(item) for key, item in value.items()}
        if isinstance(value, list):
            return [local(item) for item in value]
        if isinstance(value, str):
            return (
                value.replace("<PROJECT>", str(root))
                .replace("<WORK>", str(work))
                .replace("<HOME>", str(Path.home()))
            )
        return value

    (private / "ready.json").write_text(json.dumps(local(result["owner"]["ready"])))
    (private / "stop.json").write_text(
        json.dumps(local(result["owner"]["worker_stop"]))
    )
    raw = (STUDY / "native-forwards.jsonl").read_bytes()
    (private / "forwards.jsonl").write_bytes(raw)
    (study / "native-forwards.jsonl").write_bytes(raw)
    source = private / "runtime/semantic_search"
    source.mkdir(parents=True)
    for name in audit.COPIED:
        (source / name).write_bytes((ROOT / "src/semantic_search" / name).read_bytes())

    def absent(*args):
        raise ProcessLookupError("CPU fixture absent owned process")

    monkeypatch.setattr(audit.os, "kill", absent)
    monkeypatch.setattr(audit.os, "getpgid", absent)
    monkeypatch.setattr(audit.os, "killpg", absent)

    class Socket:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def settimeout(self, timeout):
            assert timeout == 0.2

        def connect_ex(self, address):
            assert address == ("127.0.0.1", 64161)
            return 1

    monkeypatch.setattr(audit.socket, "socket", Socket)
    return root, work, study, plan, result, private


def test_alternate_private_paths_bind_exact_public_witnesses_and_owned_absence(
    private_witnesses,
):
    root, work, study, plan, result, private = private_witnesses
    receipt = audit.cleanup_proof(root, work, study, plan, result)
    assert (
        receipt["actual_auth_file_absent"] and receipt["private_public_witnesses_exact"]
    )
    assert (
        receipt["current_pid_and_group_absent"]
        and receipt["current_listener_unreachable"]
    )
    assert not (private / "token.secret").exists()


@pytest.mark.parametrize(
    "change,reason",
    [
        ("wrong-work", "Unique actual"),
        ("group-identity", "PID/group"),
        ("bool-pid", "Typed owned"),
        ("private-mode", "mode/auth"),
        ("auth-token", "mode/auth"),
        ("auth-symlink", "mode/auth"),
        ("private-ready", "ready witness"),
        ("private-stop", "stop witness"),
        ("private-ledger", "ledger bytes"),
        ("copied-source", "copied worker"),
        ("extra-source", "source inventory"),
        ("live-pid", "disappearance"),
        ("live-group", "disappearance"),
        ("unproved-permission", "permission denied"),
        ("reachable-listener", "listener remains"),
        ("external-listener", "loopback listener"),
    ],
)
def test_unproved_private_cleanup_cannot_pass(
    private_witnesses, monkeypatch, change, reason
):
    root, work, study, plan, result, private = private_witnesses
    if change == "wrong-work":
        work = work.with_name("other-work")
    elif change == "group-identity":
        result["owner"]["stop"]["owned_group"] += 1
    elif change == "bool-pid":
        result["owner"]["pid"] = True
    elif change == "private-mode":
        private.chmod(0o755)
    elif change == "auth-token":
        (private / "token.secret").write_text("CPU placeholder, not a credential")
    elif change == "auth-symlink":
        (private / "token.secret").symlink_to(private / "absent-target")
    elif change in {"private-ready", "private-stop"}:
        path = private / ("ready.json" if change == "private-ready" else "stop.json")
        value = audit.read(path)
        value["unexpected"] = True
        path.write_text(json.dumps(value))
    elif change == "private-ledger":
        (private / "forwards.jsonl").write_text("changed\n")
    elif change == "copied-source":
        (private / "runtime/semantic_search/client.py").write_text("changed\n")
    elif change == "extra-source":
        result["owner"]["worker_source_sha256"]["untrusted.py"] = "0" * 64
    elif change == "live-pid":
        monkeypatch.setattr(audit.os, "kill", lambda *args: None)
    elif change == "live-group":
        monkeypatch.setattr(audit.os, "killpg", lambda *args: None)
    elif change == "unproved-permission":

        def denied(*args):
            raise PermissionError("permission denied")

        monkeypatch.setattr(audit.os, "kill", denied)
    elif change == "reachable-listener":

        class Socket:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def settimeout(self, timeout):
                pass

            def connect_ex(self, address):
                return 0

        monkeypatch.setattr(audit.socket, "socket", Socket)
    elif change == "external-listener":
        result["owner"]["ready"]["url"] = "http://example.com:64161"
    else:
        raise AssertionError(change)
    if change == "external-listener":
        value = audit.read(private / "ready.json")
        value["url"] = result["owner"]["ready"]["url"]
        (private / "ready.json").write_text(json.dumps(value))
    with pytest.raises((ValueError, PermissionError), match=reason):
        audit.cleanup_proof(root, work, study, plan, result)


@pytest.fixture
def frozen_sources(tmp_path, monkeypatch):
    root = tmp_path / "project"
    study = root / "results/alternate-smoke"
    study.mkdir(parents=True)
    plan = audit.read(STUDY / "frozen-plan.json")
    (study / "frozen-plan.json").write_bytes((STUDY / "frozen-plan.json").read_bytes())
    for name in plan["source_sha256"]:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((ROOT / name).read_bytes())
    original = ROOT / "results/qwen-batch-parity-supervised-2026-10-13"
    prototype = gzip.decompress(
        (original / "frozen-sources/scripts/prototype_qwen_reranker.py.gz").read_bytes()
    )

    def git(root_, revision, name):
        if name == "scripts/prototype_qwen_reranker.py":
            return prototype
        return (root_ / name).read_bytes()

    monkeypatch.setattr(audit, "git_bytes", git)
    return root, study, plan, original


def test_exact_frozen_sources_reference_ast_and_fixture_can_bind_alternate_study(
    frozen_sources,
):
    source, edited, core, warmup = audit.source_proof(*frozen_sources)
    assert source != edited and "attempt, 90" in edited
    assert (
        len(core["PINS"]) == 4 and warmup["query"] == "Locate addition of two values."
    )


@pytest.mark.parametrize(
    "change,reason",
    [
        ("missing-source", "source inventory"),
        ("changed-source", "producer bytes"),
        ("fixture-hash", "fixture reconstruction"),
        ("changed-core-ast", "scorer AST"),
        ("changed-core-prompt", "runtime pins"),
        ("changed-support-pins", "supporting source pins"),
    ],
)
def test_source_and_original_ast_proof_rejects_plausible_changed_bindings(
    frozen_sources, change, reason
):
    root, study, plan, original = frozen_sources
    if change == "missing-source":
        plan["source_sha256"].pop("src/semantic_search/client.py")
    elif change == "changed-source":
        (root / "src/semantic_search/client.py").write_text("changed\n")
    elif change == "fixture-hash":
        plan["fixture"]["edited_sha256"] = "0" * 64
    elif change == "changed-support-pins":
        plan["runtime"]["runtime"]["supporting_source_sha256"]["base.py"] = "0" * 64
    else:
        path = root / "src/semantic_search/qwen_reranker_core.py"
        text = path.read_text()
        if change == "changed-core-ast":
            text = text.replace(
                "return hashlib.sha256(data).hexdigest()",
                "return hashlib.sha256(data + b'changed').hexdigest()",
            )
        else:
            text = text.replace(
                "Names, comments, or related topics alone are insufficient.",
                "Names, comments, or related topics alone are now sufficient.",
            )
        path.write_text(text)
        plan["source_sha256"][str(path.relative_to(root))] = audit.file_digest(path)
    with pytest.raises(ValueError, match=reason):
        audit.source_proof(root, study, plan, original)


@pytest.mark.parametrize("state", ["missing", "corrupt"])
def test_offline_encoder_fails_closed_without_network_or_cache_writes(
    tmp_path, monkeypatch, state
):
    import tiktoken.load

    path = tmp_path / "ranks"
    if state == "corrupt":
        path.write_bytes(b"wrong ranks\n")

    def network_forbidden(*args, **kwargs):
        raise AssertionError("Network/cache read must never occur")

    monkeypatch.setattr(audit.tiktoken, "get_encoding", network_forbidden)
    monkeypatch.setattr(tiktoken.load, "read_file_cached", network_forbidden)
    with pytest.raises(ValueError, match="offline cl100k"):
        audit.offline_encoding(path)
    assert path.exists() is (state == "corrupt")
    if path.exists():
        assert path.read_bytes() == b"wrong ranks\n"


def test_exact_local_context_rank_bytes_are_constructed_without_download(monkeypatch):
    import tiktoken.load

    if not audit.RANKS_PATH.is_file():
        pytest.skip("Pinned local production ranks unavailable; no download permitted")

    def network_forbidden(*args, **kwargs):
        raise AssertionError("Network/cache read must never occur")

    monkeypatch.setattr(audit.tiktoken, "get_encoding", network_forbidden)
    monkeypatch.setattr(tiktoken.load, "read_file_cached", network_forbidden)
    encoder = audit.offline_encoding()
    result = audit.read(STUDY / "results.json")
    for step in result["steps"]:
        if step["phase"] == "watcher_saved_edit":
            continue
        context = audit.compact(step.get("context", step.get("response")))
        count = len(
            encoder.encode(
                json.dumps(context, ensure_ascii=False, separators=(",", ":")),
                disallowed_special=(),
            )
        )
        assert count == context["token_usage"]["tokens"]
