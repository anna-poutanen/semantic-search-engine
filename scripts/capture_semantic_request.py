"""Exclusive, audited capture of one explicitly supplied semantic search request.

This component has no CLI, model loader, corpus loader or benchmark runner. The
caller supplies a backend and its own classification/bindings. Source hashes and
execution receipts do not establish checkpoint provenance, retrieval quality, a
qualified control, or native warm latency. Those remain a frozen runner's job.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib.util
import inspect
import json
import math
import os
from pathlib import Path
import subprocess
import sys

from semantic_search.context import compact_result, encoding, serialize_context
from semantic_search.semantic_context_adapter import make_live_semantic_postprocessor


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "semantic_request_capture_v1"
LIMITS = ("max_results", "source_budget", "token_budget")


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _json_bytes(value):
    # Preserve dictionary insertion order. compact_result includes the literal
    # nested scope map; sorting it during storage would change the actual tool
    # text after roundtrip even when the JSON values remained equal.
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _literal_json(value):
    if value is None or type(value) in (str, bool, int):
        return True
    if type(value) is float:
        return math.isfinite(value)
    if type(value) is list:
        return all(_literal_json(item) for item in value)
    if type(value) is dict:
        return all(type(key) is str and _literal_json(item) for key, item in value.items())
    return False


def _source_hashes():
    package_root = (ROOT / "src/semantic_search").resolve()
    for name, module in list(sys.modules.items()):
        if name == "semantic_search" or name.startswith("semantic_search."):
            location = getattr(module, "__file__", None)
            if location is None or not Path(location).resolve().is_relative_to(package_root):
                raise ValueError("Executing semantic_search package differs from the captured source root")
    # Hash the complete product package, including extraction, retrieval,
    # packing and source helpers, rather than guess at transitive imports.
    paths = sorted((ROOT / "src/semantic_search").rglob("*.py"))
    paths += [ROOT / "scripts/audit_semantic_context.py", Path(__file__).resolve()]
    return {path.relative_to(ROOT).as_posix(): _sha(path.read_bytes()) for path in paths}


def _git_state():
    def git(*args):
        result = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True,
                                text=True, check=False, timeout=10)
        return result.stdout.strip() if result.returncode == 0 else None

    try:
        head, status = git("rev-parse", "HEAD"), git("status", "--porcelain", "--untracked-files=no")
    except (OSError, subprocess.TimeoutExpired):
        return {"head": None, "tracked_dirty": None, "status_available": False}
    return {"head": head, "tracked_dirty": bool(status) if status is not None else None,
            "tracked_status": status, "status_available": head is not None and status is not None}


def _audit_module():
    path = ROOT / "scripts/audit_semantic_context.py"
    spec = importlib.util.spec_from_file_location("semantic_request_independent_audit", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _error_info(error):
    try:
        message = str(error)
    except BaseException:
        message = "Exception message could not be rendered"
    return {"error_type": f"{type(error).__module__}.{type(error).__qualname__}", "message": message}


def _safe_snapshot(value, seen=None):
    """Keep JSON-compatible partial evidence even if ordinary capture failed.

    Unsupported or cyclic objects receive explicit type markers, never invented
    values. The failure marker therefore remains serializable without claiming
    that an unserializable original object was preserved exactly.
    """
    if value is None or type(value) in (str, bool, int):
        if type(value) is str:
            try:
                value.encode("utf-8")
            except UnicodeError:
                return {"capture_unserializable_type": "str", "reason": "invalid Unicode"}
        return value
    if type(value) is float:
        return value if math.isfinite(value) else {
            "capture_unserializable_type": "float", "reason": "nonfinite"}
    seen = set() if seen is None else seen
    if isinstance(value, (dict, list, tuple)):
        if id(value) in seen:
            return {"capture_unserializable_type": type(value).__name__, "reason": "cycle"}
        seen.add(id(value))
        try:
            if isinstance(value, dict):
                if any(type(key) is not str for key in value):
                    return {"capture_unserializable_type": "dict", "reason": "non-string keys",
                            "entries": [[_safe_snapshot(key, seen), _safe_snapshot(item, seen)]
                                        for key, item in value.items()]}
                return {key: _safe_snapshot(item, seen) for key, item in value.items()}
            return [_safe_snapshot(item, seen) for item in value]
        finally:
            seen.remove(id(value))
    return {"capture_unserializable_type": f"{type(value).__module__}.{type(value).__qualname__}"}


def _work(events):
    started = completed = failed = encoded = malformed = 0
    starts, terminals = set(), set()
    for event in events:
        if (not isinstance(event, dict) or event.get("schema") != "qwen_embedding_forward_v1"
                or event.get("status") not in ("started", "completed", "failed")):
            malformed += 1
            continue
        status, index, count = event["status"], event.get("forward_index"), event.get("batch_input_count")
        if type(index) is not int or index < 0 or type(count) is not int or count < 1:
            malformed += 1
            continue
        if status == "started":
            started += 1
            if index in starts:
                malformed += 1
            starts.add(index)
        else:
            completed += status == "completed"
            failed += status == "failed"
            encoded += count
            if index in terminals:
                malformed += 1
            terminals.add(index)
    return {"started_forward_calls": started, "completed_forward_calls": completed,
            "failed_forward_calls": failed, "reported_model_forward_calls": completed + failed,
            "reported_encoded_texts": encoded, "malformed_events": malformed,
            "terminal_coverage_complete": not malformed and starts == terminals}


def _provider_receipts(records):
    receipts, seen = [], set()
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("provider_receipts"), list):
            continue
        for receipt in record["provider_receipts"]:
            if id(receipt) not in seen:
                receipts.append(receipt)
                seen.add(id(receipt))
    return receipts


def _counts(forwards, receipts):
    # A failed provider may retain both its completed and failed receipt. Count
    # the longest consistent extra-query event sequence once, not once per copy.
    extra, consistent = [], True
    for receipt in receipts:
        if not isinstance(receipt, dict):
            consistent = False
            continue
        candidate = receipt.get("extra_query_forward_events", [])
        if not isinstance(candidate, list):
            consistent = False
            continue
        try:
            common = min(len(extra), len(candidate))
            if _json_bytes(extra[:common]) != _json_bytes(candidate[:common]):
                consistent = False
            elif len(candidate) > len(extra):
                extra = candidate
        except (TypeError, ValueError, OverflowError, UnicodeError):
            consistent = False
    documents = _work([event for event in forwards
                       if isinstance(event, dict) and event.get("purpose") == "documents"])
    query = _work([event for event in forwards
                   if isinstance(event, dict) and event.get("purpose") == "retrieval_query"])
    clauses = _work(extra)
    unknown = sum(not isinstance(event, dict)
                  or event.get("purpose") not in ("documents", "retrieval_query") for event in forwards)
    return {"documents": documents, "retrieval_query": query, "extra_queries": clauses,
            "unknown_external_events": unknown, "extra_receipts_consistent": consistent,
            "reported_total_model_forward_calls": sum(item["reported_model_forward_calls"]
                for item in (documents, query, clauses)),
            "reported_request_document_inference_calls": documents["reported_model_forward_calls"],
            "receipt_coverage_complete": not unknown and consistent
                and all(item["terminal_coverage_complete"] for item in (documents, query, clauses)),
            "scope": "Reported execution receipts; started events alone do not prove a model forward"}


def _validate_forward_bindings(response, forwards, receipts, counts):
    external_query = [{key: value for key, value in event.items() if key != "purpose"}
                      for event in forwards if isinstance(event, dict) and event.get("purpose") == "retrieval_query"]
    for receipt in receipts:
        if isinstance(receipt, dict) and receipt.get("status") == "active":
            if _json_bytes(external_query) != _json_bytes(receipt.get("original_query_forward_events")):
                raise ValueError("Original query receipts differ from external executing-query receipts")
    stats = response.get("indexstats", {})
    if response.get("retrieval") != "bm25" and stats.get("total_windows", 0) > 0:
        if counts["retrieval_query"]["reported_model_forward_calls"] != 1:
            raise ValueError("Nonempty embedding search requires its actual original query forward")
        if stats.get("indexed_windows", 0) > 0 and counts["documents"]["reported_model_forward_calls"] == 0:
            raise ValueError("Cold indexed windows require independently recorded document forwards")


class SemanticRequestCapture:
    """Capture one request with stable, explicit backend research hooks.

    Hook callbacks retain references only. Copies, serialization, source audits
    and disk writes happen after ``backend.search`` returns or raises, outside
    that backend's reported timings. The component makes no warm/native claim.
    The attempt's parent directory must already exist. Hooks may only be called
    inside ``run``; perform any separately qualified warmup before installing them.
    """

    def __init__(self, output_dir, *, bindings):
        if not isinstance(bindings, dict) or not _literal_json(bindings):
            raise ValueError("Capture bindings must be finite literal JSON data")
        self.output_dir = Path(output_dir).resolve()
        self.bindings = json.loads(_json_bytes(bindings))
        self._bound_bindings = _json_bytes(self.bindings)
        self._used = False
        self._created = False
        self._recording = False
        self._records, self._forwards = [], []
        self.postprocessor = make_live_semantic_postprocessor(capture=self.record)
        self.forward_observer = self.record_forward  # Bound method identity must remain stable.

    def record(self, event):
        if not self._recording:
            raise RuntimeError("Capture hooks may only run inside the captured request")
        self._records.append(event)

    def record_forward(self, event):
        if not self._recording:
            raise RuntimeError("Capture hooks may only run inside the captured request")
        self._forwards.append(event)

    @staticmethod
    def _sync_directory(directory):
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _write_json(self, filename, value):
        payload = _json_bytes(value) + b"\n"
        with (self.output_dir / filename).open("xb") as target:
            target.write(payload)
            target.flush()
            os.fsync(target.fileno())
        self._sync_directory(self.output_dir)
        return _sha(payload)

    def _failure(self, error, phase):
        receipts = _provider_receipts(self._records)
        self._write_json("failure.json", {"schema": SCHEMA, "status": "failed", "phase": phase,
            **_safe_snapshot(_error_info(error)), "bindings": json.loads(self._bound_bindings),
            "captured_events": _safe_snapshot(self._records),
            "external_forward_events": _safe_snapshot(self._forwards),
            "provider_receipts": _safe_snapshot(receipts),
            "forward_counts": _counts(self._forwards, receipts),
            "evidence_scope": "Partial execution evidence; no quality or warm-latency conclusion"})

    def run(self, backend, **search_kwargs):
        if self._used:
            raise RuntimeError("A SemanticRequestCapture attempt can run only once")
        self._used = True
        if (getattr(backend, "_research_postprocessor", None) is not self.postprocessor
                or getattr(backend, "_research_forward_observer", None) is not self.forward_observer):
            raise ValueError("Backend research hooks must be the capture's exact stable attributes")
        if not _literal_json(search_kwargs):
            raise ValueError("Captured search arguments must be finite literal JSON data")
        parameters = inspect.signature(backend.search).parameters
        limits = {}
        for name in LIMITS:
            value = search_kwargs.get(name, parameters[name].default if name in parameters else inspect.Parameter.empty)
            if value is inspect.Parameter.empty:
                raise ValueError("An explicit or inspectable search limit is required: " + name)
            limits[name] = value
        self.output_dir.mkdir(exist_ok=False)
        self._created = True
        bindings = json.loads(self._bound_bindings)
        phase = "attempt_directory_sync"
        try:
            # The directory's name lives in its parent, so syncing files and
            # the new directory alone would not make this exclusive attempt
            # durable across power loss.
            self._sync_directory(self.output_dir.parent)
            phase = "pre_request_manifest"
            source_hashes = _source_hashes()
            manifest_sha = self._write_json("manifest.json", {"schema": SCHEMA,
                "classification": bindings.get("classification", "unqualified_one_request_capture"),
                "bindings": bindings, "created_at": datetime.now(timezone.utc).isoformat(),
                "source_sha256": source_hashes, "git": _git_state(),
                "claims": {"quality_gate": False, "checkpoint_provenance": False,
                           "qualified_control": False, "native_warm_latency": False},
                "timing_boundary": "Capture callbacks retain references; copies/audits/serialization follow search"})
            request_sha = self._write_json("request.json", {"schema": SCHEMA,
                "search_kwargs": search_kwargs, "limits": limits, "bindings": bindings})
            phase = "backend_search"
            self._recording = True
            try:
                response = backend.search(**search_kwargs)
            finally:
                self._recording = False
            phase = "capture_snapshot"
            records, forwards = deepcopy(self._records), deepcopy(self._forwards)
            complete = [record for record in records if isinstance(record, dict)
                        and "reference_response" in record and "semantic_receipt" in record]
            if len(complete) != 1:
                raise ValueError("Exactly one complete semantic postprocessor record is required")
            record = complete[0]
            if record.get("limits") != limits:
                raise ValueError("Captured context limits differ from the recorded request")
            phase = "final_response_binding"
            auditor, counter = _audit_module(), encoding()
            if not auditor.same_source_response(record.get("response"), response):
                raise ValueError("Captured response differs from the actual final backend response")
            record["response"] = deepcopy(response)
            payload = serialize_context(compact_result(response))
            record.update(compact_payload=payload, compact_sha256=_sha(payload.encode()),
                actual_compact_tokens=len(counter.encode(payload, disallowed_special=())))
            receipts = _provider_receipts(records)
            counts = _counts(forwards, receipts)
            _validate_forward_bindings(response, forwards, receipts, counts)
            if (not counts["receipt_coverage_complete"]
                    or any(counts[name]["failed_forward_calls"] for name in
                           ("documents", "retrieval_query", "extra_queries"))):
                raise ValueError("Successful request has incomplete or failed forward receipts")
            record.update(external_forward_events=forwards, forward_counts=counts,
                capture={"schema": SCHEMA, "manifest_sha256": manifest_sha,
                    "request_sha256": request_sha, "captured_event_count": len(records),
                    "classification": bindings.get("classification", "unqualified_one_request_capture"),
                    "claims": "One-request component only; no quality, provenance or warm-latency conclusion"})
            phase = "independent_audit"
            auditor.verify_record(record, counter)
            phase = "json_roundtrip"
            encoded = _json_bytes(record)
            restored = json.loads(encoded)
            if _json_bytes(restored) != encoded:
                raise ValueError("Captured JSON roundtrip differs")
            auditor.verify_record(restored, counter)
            phase = "source_binding_recheck"
            if _source_hashes() != source_hashes:
                raise ValueError("Result-affecting source changed during the capture")
            phase = "persist_success"
            record_sha = self._write_json("record.json", restored)
            self._write_json("audit.json", {"schema": SCHEMA, "status": "passed",
                "independent_audit": True, "json_roundtrip_audit": True,
                "source_binding_unchanged": True, "record_sha256": record_sha,
                "manifest_sha256": manifest_sha, "request_sha256": request_sha,
                "compact_sha256": record["compact_sha256"],
                "actual_compact_tokens": record["actual_compact_tokens"], "forward_counts": counts,
                "claims": "Internal capture consistency only; trusted source/control/model bindings remain future work"})
            return response
        except BaseException as error:
            try:
                self._failure(error, phase)
            except BaseException as recorder_error:
                if recorder_error is error:
                    raise
                raise error from recorder_error
            raise
