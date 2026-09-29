"""Independent synthetic mutation fixtures: no producer imports or inference."""
import copy
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("independent_vscode_qwen_audit", ROOT / "scripts/audit_vscode_qwen.py")
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)

PREFIX = ('<|im_start|>system\nJudge whether the Document meets the requirements based '
          'on the Query and the Instruct provided. Note that the answer can only be '
          '"yes" or "no".<|im_end|>\n<|im_start|>user\n')
SUFFIX = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
INSTRUCTION = ("Given a requested code behavior, retrieve executable code that directly "
               "implements the behavior or any necessary supporting part of that behavior. "
               "Names, comments, or related topics alone are insufficient.")
COUNTERS = ("requests", "attempted_pairs", "completed_pairs", "attempted_forwards", "completed_forwards")
WARMUP = {"query": "Locate addition of two values.",
          "source": "def add(left, right):\n    return left + right\n"}


def digest(value):
    return hashlib.sha256(value).hexdigest()


class FakeTokenizer:
    def encode(self, text, add_special_tokens=False):
        assert add_special_tokens is False
        if text in ("yes", "no"):
            return [90001 if text == "yes" else 90002]
        return [ord(char) for char in text]


def literal_pair(query, source):
    """Fixture inputs are short whole sources, independently tokenized here."""
    tokenizer = FakeTokenizer()
    header = f"<Instruct>: {INSTRUCTION}\n<Query>: {query}\n<Document>: "
    ids = tokenizer.encode(PREFIX) + tokenizer.encode(header + source) + tokenizer.encode(SUFFIX)
    lines = len(re.findall(r"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+$", source))
    raw = source.encode()
    return {"input_ids": ids, "source": source, "source_sha256": digest(raw), "delivered_sha256": digest(raw),
            "source_lines": lines, "delivered_lines": lines, "source_utf8_bytes": len(raw),
            "delivered_utf8_bytes": len(raw), "truncated": False, "total_tokens": len(ids),
            "query_sha256": digest(query.encode())}


def scalar(yes, no):
    maximum = max(yes, no)
    y, n = math.exp(yes - maximum), math.exp(no - maximum)
    return {"yes_logit": yes, "no_logit": no, "logit_difference": yes - no, "yes_rank_signal": y / (y + n)}


def native_fixture():
    observations, rows = [], []
    number = 1
    warm = literal_pair(**WARMUP)
    identity = {"request_id": "generic-warmup", "batch_size": 1, "forward_number": 1, "warmup": True,
                "input_ids_sha256": digest(json.dumps(warm["input_ids"], separators=(",", ":")).encode()),
                **{key: warm[key] for key in ("query_sha256", "source_sha256", "delivered_sha256", "total_tokens")}}
    rows.extend([identity | {"event": "started"}, identity | {"event": "completed", "score": scalar(0.5, 0.0)}])
    for dirty in (False, True):
        query = "Find the unsaved ratio" if dirty else "Find the saved behavior"
        sources, receipts = [], {}
        for index in range(5):
            number += 1
            text = f"def literal_{index}():\r\n    return {index + int(dirty)}\r\n"
            identifier, request = f"{'dirty' if dirty else 'saved'}-{index}", f"actual-{number}"
            built, score = literal_pair(query, text), scalar(float(index) - 2.5, 0.25)
            result = {key: value for key, value in built.items() if key not in ("input_ids", "source")} | score
            receipts[identifier] = {"request_id": request, "batch_size": 1, "result": result,
                                    "counts": {key: number for key in COUNTERS}}
            sources.append({"id": identifier, "source": text, "truncated": False,
                            "qwen_score": score["logit_difference"], "h2o_score": None})
            identity = {"request_id": request, "batch_size": 1, "forward_number": number, "warmup": False,
                        "input_ids_sha256": digest(json.dumps(built["input_ids"], separators=(",", ":")).encode()),
                        **{key: built[key] for key in ("query_sha256", "source_sha256", "delivered_sha256", "total_tokens")}}
            rows.extend([identity | {"event": "started"}, identity | {"event": "completed", "score": score}])
        observations.append({"request_id": "dirty-search" if dirty else "saved-search", "query": query,
                             "settings": {"overlays": [{"path": "src/delta.ts"}]} if dirty else {},
                             "response": {"rerank": True, "rerank_provider": "qwen", "candidate_windows": 5,
                                          "retrieval_candidate_windows": 5, "reranker_batch_size": 1,
                                          "results": sources, "reranker_receipts": receipts}})
    return rows, observations, {key: 11 for key in COUNTERS}, FakeTokenizer(), copy.deepcopy(WARMUP)


def embedding_fixture(cancel_entry=0, cancel_status="cancelled"):
    attempts = [{"request_id": f"request-{index}", "query": f"Literal behavior {index}", "settings": {},
                 "status": "completed"} for index in range(7)]
    attempts[-1]["settings"] = {"overlays": [{"path": "src/delta.ts"}]}
    attempts.append({"request_id": "cancelled", "query": "Literal behavior" + audit.CANCEL_SUFFIX,
                     "settings": {}, "status": cancel_status})
    entries = [{"kind": "documents", "request_id": None, "status": "completed", "document_texts": 5}]
    entries.extend({"kind": "query", "request_id": item["request_id"], "status": "completed",
                    "query_sha256": digest(item["query"].encode())} for item in attempts[:7])
    entries.append({"kind": "documents", "request_id": attempts[6]["request_id"], "status": "completed", "document_texts": 1})
    if cancel_entry:
        entries.append({"kind": "query", "request_id": "cancelled", "status": "completed",
                        "query_sha256": digest(attempts[-1]["query"].encode())})
    for number, entry in enumerate(entries, 1):
        entry["number"] = number
    observations = [{"request_id": item["request_id"]} for item in attempts if item["status"] == "completed"]
    counts = {"query_calls": 7 + cancel_entry, "document_calls": 2, "document_texts": 6}
    return attempts, entries, observations, counts


def publication_fixture():
    return {"checks_passed": True, "publication_rule": "Empty actual signals.jsonl with exact artifact hash required",
            "artifact_sha256": {"signals.jsonl": digest(b"")}}, b""


def host_fixture():
    return {"passed": True, "host": {"passed": True, "qwen_reranker": {"passed": True}},
            "relay_close": {"records_stable": True, "close_grace_ms": 500,
                            "unfinished_receipts": 0, "forced_incomplete_receipts": 0},
            "relay_close_checks": {"records_stable": True, "no_unfinished_receipts": True,
                                   "final_receipts_without_transport_error": True, "no_hold_released_by_close": True},
            "owned_profile_removed": True, "receipts": [{"endpoint": "/status", "status": 200}],
            "exit": {"code": 0, "completed_cleanly": True, "interrupted": []}}


class CharacterEncoding:
    """Synthetic deterministic counter, not cl100k or a real usage estimate."""
    def encode(self, text, disallowed_special=()):
        assert disallowed_special == ()
        return list(range(len(text)))


def http_fixture(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    saved = "def literal():\n    return 1\n"
    dirty = "export function ratio(before: number, after: number): number {\n  return after / before;\n}\n"
    (repo / "src/inventory.py").write_text(saved)
    observations, receipts = [], []
    for index in range(8):
        query = f"Locate literal behavior {index}" if index < 7 else "Locate literal behavior" + audit.CANCEL_SUFFIX
        request = {"query": query, "request_id": f"http-{index}", "retrieval": "hybrid" if index in (4, 5, 6) else "dense",
                   "rerank": index in (5, 6), "candidates": 24 if index in (4, 5, 6) else 4,
                   "max_results": 5, "source_budget": 12000, "token_budget": 2000, "include_dependencies": False}
        if request["rerank"]:
            request["rerank_provider"] = "qwen"
        scope = {"source_root": "src", "membership": "saved implementations; Git-tracked files only in Git projects",
                 "source_exclusions": [], "exclusions_omitted": 0}
        text, path = saved, "src/inventory.py"
        if index == 6:
            text, path = dirty, "src/delta.ts"
            request["overlays"] = [{"path": path, "text": dirty, "version": 2, "content_hash": digest(dirty.encode())}]
            scope.update(membership="tracked implementations with request-only editor text overrides", source_state="overlay",
                         saved_source_manifest=digest(saved.encode()),
                         overlays=[{"path": path, "version": 2, "content_hash": digest(dirty.encode())}])
        source = {"id": f"window-{index}", "path": path, "symbol": "ratio" if index == 6 else "literal",
                  "start_line": 1, "end_line": 3 if index == 6 else 2, "window_end_line": 3 if index == 6 else 2,
                  "source": text, "source_hash": digest(text.encode()), "content_hash": digest(text.encode()), "truncated": False}
        if index == 6:
            source.update(source_origin="overlay", buffer_version=2, buffer_hash=digest(dirty.encode()))
        response = {"query": query, "retrieval": request["retrieval"], "rerank": request["rerank"],
                    "repo": "<WORK>/repo", "results": [source], "scope": scope, "total_source_chars": len(text),
                    "token_usage": {"encoding": "cl100k_base", "tokens": 0, "budget": 2000, "truncated": False}}
        if request["rerank"]:
            response["rerank_provider"] = "qwen"
        compact_row = {key: source[key] for key in ("path", "symbol", "start_line", "end_line", "source", "source_hash", "truncated")}
        compact_row.update(origin="retrieval", related_to=None)
        for key in ("source_origin", "buffer_version", "buffer_hash"):
            if key in source:
                compact_row[key] = source[key]
        envelope = {"query": query, "match_status": "candidates", "results": [compact_row], "budget_exhausted": False,
                    "token_usage": response["token_usage"], "scope": scope}
        for _ in range(4):
            response["token_usage"]["tokens"] = len(json.dumps(envelope, ensure_ascii=False, separators=(",", ":")))
        receipt = {"endpoint": "/search", "status": 409 if index == 7 else 200, "request": request,
                   "overlay_input_bindings_verified": True,
                   "request_sha256": digest(json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode()),
                   "returned_source_checks": [{key: source[key] for key in ("path", "symbol", "start_line", "end_line", "source_hash")} | {"exact_source": True}],
                   "response": copy.deepcopy(response) | {"repo": "<fixture>"}}
        receipts.append(receipt)
        if index < 7:
            observations.append({"request_id": request["request_id"], "query": query,
                                 "settings": {key: value for key, value in request.items() if key != "query"}, "response": response})
    receipts.append({"endpoint": "/cancel", "status": 200, "request": {"request_id": "http-7"}})
    return observations, {"receipts": receipts}, repo, CharacterEncoding()


@pytest.mark.parametrize("source", ["def ascii():\n    return 1\n", "\ufefffunction x() {\r\n  return 'é\u2028𐐷';\r\n}\r\n", "def final():\r    return 2"])
def test_independent_pair_matches_literal_tokens_and_physical_source_lines(source):
    expected = literal_pair("Locate literal code", source)
    del expected["source"]  # Audit API returns metadata/IDs, rather than duplicating literal source text.
    assert audit.pair("Locate literal code", source, FakeTokenizer()) == expected


def test_complete_native_inventory_reconciles_without_inference():
    audit.validate_native(*native_fixture())


@pytest.mark.parametrize("counter", COUNTERS)
@pytest.mark.parametrize("value", [999, True, 2.0])
def test_native_rejects_every_receipt_counter_wrong_integer_bool_or_float(counter, value):
    rows, observations, counts, tokenizer, warmup = native_fixture()
    observations[0]["response"]["reranker_receipts"]["saved-0"]["counts"][counter] = value
    with pytest.raises((AssertionError, ValueError)):
        audit.validate_native(rows, observations, counts, tokenizer, warmup)


@pytest.mark.parametrize("counter", COUNTERS)
@pytest.mark.parametrize("value", [999, True, 11.0])
def test_native_final_aggregate_counters_require_exact_integer_for_every_field(counter, value):
    rows, observations, counts, tokenizer, warmup = native_fixture()
    counts[counter] = value
    with pytest.raises((AssertionError, ValueError)):
        audit.validate_native(rows, observations, counts, tokenizer, warmup)


@pytest.mark.parametrize("change", ["missingevent", "duplicateevent", "eventorder", "warmup", "batch", "request",
                                  "inputhash", "source", "tokens", "provider", "candidatecount", "missingwindow",
                                  "clipped", "returnedscore", "binaryscore", "nonfinite", "finalcount"])
def test_native_rejects_incomplete_mismatched_or_fabricated_evidence(change):
    rows, observations, counts, tokenizer, warmup = native_fixture()
    response = observations[0]["response"]
    receipt = response["reranker_receipts"]["saved-0"]
    if change == "missingevent":
        rows.pop()
    elif change == "duplicateevent":
        rows.append(copy.deepcopy(rows[-1]))
    elif change == "eventorder":
        rows[2], rows[3] = rows[3], rows[2]
    elif change == "warmup":
        rows[2]["warmup"] = True
    elif change == "batch":
        rows[2]["batch_size"] = 2
    elif change == "request":
        rows[2]["request_id"] = "other"
    elif change == "inputhash":
        rows[2]["input_ids_sha256"] = "0" * 64
    elif change == "source":
        response["results"][0]["source"] += "# invented\n"
    elif change == "tokens":
        receipt["result"]["total_tokens"] += 1
    elif change == "provider":
        response["rerank_provider"] = "h2o"
    elif change == "candidatecount":
        response["candidate_windows"] = 4
    elif change == "missingwindow":
        response["results"].pop()
    elif change == "clipped":
        response["results"][0]["truncated"] = True
    elif change == "returnedscore":
        response["results"][0]["qwen_score"] = 999.0
    elif change == "binaryscore":
        receipt["result"]["yes_rank_signal"] = 0.99
    elif change == "nonfinite":
        receipt["result"]["yes_logit"] = float("nan")
    else:
        counts["requests"] = 10
    with pytest.raises((AssertionError, ValueError)):
        audit.validate_native(rows, observations, counts, tokenizer, warmup)


@pytest.mark.parametrize("cancel_entry,cancel_status", [(0, "cancelled"), (1, "cancelled"), (1, "completed")])
def test_embedding_accounts_for_each_actual_cancellation_stage(cancel_entry, cancel_status):
    audit.validate_embedding(*embedding_fixture(cancel_entry, cancel_status))


@pytest.mark.parametrize("change", ["missingattempt", "duplicateid", "unfinished", "wrongquery", "unknownrequest",
                                  "extraquery", "wrongcount", "extradocument", "fakecompletedcancel", "failedcancel"])
def test_embedding_rejects_dropped_invented_or_mismatched_api_entries(change):
    attempts, entries, observations, counts = embedding_fixture()
    if change == "missingattempt":
        attempts.pop()
    elif change == "duplicateid":
        attempts[-1]["request_id"] = attempts[0]["request_id"]
    elif change == "unfinished":
        entries[1]["status"] = "entered"
    elif change == "wrongquery":
        entries[1]["query_sha256"] = "0" * 64
    elif change == "unknownrequest":
        entries[1]["request_id"] = "not-observed"
    elif change == "extraquery":
        entries.append(dict(entries[1], number=len(entries) + 1))
    elif change == "wrongcount":
        counts["query_calls"] = 8
    elif change == "extradocument":
        entries.append(dict(entries[0], number=len(entries) + 1))
    elif change == "fakecompletedcancel":
        attempts[-1]["status"] = "completed"
        observations.append({"request_id": "cancelled"})
    else:
        attempts[-1]["status"] = "failed"
    with pytest.raises((AssertionError, ValueError)):
        audit.validate_embedding(attempts, entries, observations, counts)


def test_publication_requires_actual_empty_bound_journal():
    audit.validate_publication(*publication_fixture())


@pytest.mark.parametrize("change", ["late-signal", "rebound-signal", "digest", "rule", "failed", "missing-digest"])
def test_publication_rejects_late_signal_even_when_previous_json_flag_passed(change):
    certificate, journal = publication_fixture()
    if change in ("late-signal", "rebound-signal"):
        journal = b'{"signal":"SIGTERM","received_at_utc":"synthetic-event"}\n'
        if change == "rebound-signal":
            certificate["artifact_sha256"]["signals.jsonl"] = digest(journal)
    elif change == "digest":
        certificate["artifact_sha256"]["signals.jsonl"] = "0" * 64
    elif change == "rule":
        del certificate["publication_rule"]
    elif change == "failed":
        certificate["checks_passed"] = False
    else:
        del certificate["artifact_sha256"]["signals.jsonl"]
    with pytest.raises((AssertionError, ValueError)):
        audit.validate_publication(certificate, journal)


def test_actual_host_qualification_requires_clean_final_close_and_no_interruption():
    audit.validate_host(host_fixture())


@pytest.mark.parametrize("change", ["hostfailed", "qwenfailed", "unstable", "unfinished", "forced", "floatgrace",
                                  "falsechecks", "emptychecks", "profile", "transport", "incomplete", "holdclose", "exit", "interrupted"])
def test_host_rejects_partial_late_transport_or_unclean_evidence(change):
    host = host_fixture()
    if change == "hostfailed":
        host["host"]["passed"] = False
    elif change == "qwenfailed":
        host["host"]["qwen_reranker"]["passed"] = False
    elif change == "unstable":
        host["relay_close"]["records_stable"] = False
    elif change == "unfinished":
        host["relay_close"]["unfinished_receipts"] = 1
    elif change == "forced":
        host["relay_close"]["forced_incomplete_receipts"] = 1
    elif change == "floatgrace":
        host["relay_close"]["close_grace_ms"] = 500.0
    elif change == "falsechecks":
        host["relay_close_checks"]["records_stable"] = False
    elif change == "emptychecks":
        host["relay_close_checks"] = {}
    elif change == "profile":
        host["owned_profile_removed"] = False
    elif change == "transport":
        host["receipts"][0]["transport_error"] = "late failure"
    elif change == "incomplete":
        host["receipts"][0]["incomplete"] = True
    elif change == "holdclose":
        host["receipts"][0]["hold_released_by_close"] = True
    elif change == "exit":
        host["exit"]["code"] = 1
    else:
        host["exit"]["interrupted"] = ["SIGTERM"]
    with pytest.raises((AssertionError, ValueError)):
        audit.validate_host(host)


def test_http_reconciles_distinct_saved_dirty_and_cancel_receipts_without_producer_imports(tmp_path):
    audit.validate_http(*http_fixture(tmp_path))


@pytest.mark.parametrize("change", ["retainedpayload", "settings", "duplicaterequest", "tokencount", "literal",
                                  "contenthash", "overlayhash", "overlayversion", "provider", "missingcancel"])
def test_http_rejects_cross_record_settings_source_caps_and_binding_corruptions(tmp_path, change):
    observations, host, repo, encoding = http_fixture(tmp_path)
    if change == "retainedpayload":
        host["receipts"][0]["response"]["repo"] = "<wrong-fixture>"
    elif change == "settings":
        observations[0]["settings"]["candidates"] = 50
    elif change == "duplicaterequest":
        host["receipts"][1]["request"]["request_id"] = "http-0"
    elif change == "tokencount":
        observations[0]["response"]["token_usage"]["tokens"] += 1
        host["receipts"][0]["response"]["token_usage"]["tokens"] += 1
    elif change in ("literal", "contenthash"):
        field = "source" if change == "literal" else "content_hash"
        value = "invented source" if change == "literal" else "0" * 64
        observations[0]["response"]["results"][0][field] = value
        host["receipts"][0]["response"]["results"][0][field] = value
    elif change == "overlayhash":
        host["receipts"][6]["request"]["overlays"][0]["content_hash"] = "0" * 64
    elif change == "overlayversion":
        observations[6]["response"]["results"][0]["buffer_version"] = 3
        host["receipts"][6]["response"]["results"][0]["buffer_version"] = 3
    elif change == "provider":
        host["receipts"][5]["request"]["rerank_provider"] = "h2o"
    else:
        host["receipts"].pop()
    with pytest.raises((AssertionError, ValueError)):
        audit.validate_http(observations, host, repo, encoding)
