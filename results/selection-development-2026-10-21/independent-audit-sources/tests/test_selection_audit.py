"""Adversarial checks of the independent auditor, without producer imports."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("selection_audit", ROOT / "scripts/audit_selection.py")
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


class BytesCounter:
    def encode(self, text, **kwargs):
        return text.encode()


def fixture():
    source = "one\ntwo\nthree\nfour\n"
    full = {"id": "parent", "path": "a.py", "symbol": "parent", "start_line": 1, "end_line": 4,
            "text": source, "content_hash": audit.sha(source.encode())}
    row = {**{k: full[k] for k in ("id", "path", "symbol", "start_line", "end_line", "content_hash")},
           "source": source, "source_hash": audit.sha(source.encode()), "window_end_line": 4,
           "origin": "retrieval", "related_to": None, "truncated": False}
    response = {"query": "query", "results": [row], "total_source_chars": len(source),
                "budget_exhausted": False,
                "token_usage": {"encoding": "cl100k_base", "tokens": 0, "budget": 4096, "truncated": False}}
    for _ in range(10):
        response["token_usage"]["tokens"] = len(audit.compact_payload(response).encode())
    return full, response


def test_physical_lines_keep_unicode_separators_and_original_endings():
    assert audit.physical_lines("a\u2028b\r\nc\rd\ne") == ["a\u2028b\r\n", "c\r", "d\n", "e"]
    assert audit.physical_lines("") == []
    assert audit.physical_lines("\n") == ["\n"]


def test_literal_serializer_uses_public_fields_and_provenance_in_exact_order():
    _, response = fixture()
    row = response["results"][0]
    row.update(source_origin="saved", buffer_version=4, buffer_hash="hash", private="omit")
    compact = json.loads(audit.compact_payload(response))
    assert list(compact) == ["query", "match_status", "results", "budget_exhausted", "token_usage"]
    assert list(compact["results"][0]) == ["path", "symbol", "start_line", "end_line", "source", "source_hash",
        "truncated", "origin", "related_to", "source_origin", "buffer_version", "buffer_hash"]
    assert "private" not in compact["results"][0] and "id" not in compact["results"][0]


def test_evidence_requires_one_whole_actual_excerpt_or_alternative_and_all_groups():
    groups = [[{"path": "a", "evidence_lines": [1, 6]}],
              [{"path": "b", "evidence_lines": [8, 10]}, {"path": "c", "evidence_lines": [2, 4]}]]
    rows = [{"path": "a", "start_line": 1, "end_line": 3},
            {"path": "a", "start_line": 4, "end_line": 6},
            {"path": "c", "start_line": 2, "end_line": 4}]
    assert audit.evidence_ranks(rows, groups) == [None, 3]


@pytest.mark.parametrize("mutation,reason", [
    (lambda r: r.update(id="invented"), "Noncanonical"),
    (lambda r: r.update(source="changed\n"), "Actual source"),
    (lambda r: r.update(source_hash="wrong"), "Actual source"),
    (lambda r: r.update(content_hash="wrong"), "Full source"),
    (lambda r: r.update(window_end_line=5), "Actual/window"),
    (lambda r: r.update(end_line=5), "Actual/window"),
    (lambda r: r.update(truncated=True), "Full source"),
    (lambda r: r.update(origin="dependency", related_to="missing"), "Origin/parent"),
])
def test_noncanonical_rows_bounds_hashes_and_origins_rejected(mutation, reason):
    full, response = fixture()
    mutation(response["results"][0])
    with pytest.raises(ValueError, match=reason):
        audit.verify_response(response, {full["id"]: full}, {"a.py": full["text"]}, BytesCounter())


def test_exact_tokens_character_totals_and_caps_recounted_independently():
    full, response = fixture()
    audit.verify_response(response, {"parent": full}, {"a.py": full["text"]}, BytesCounter())
    response["token_usage"]["tokens"] += 1
    with pytest.raises(ValueError, match="Exact compact"):
        audit.verify_response(response, {"parent": full}, {"a.py": full["text"]}, BytesCounter())
    _, response = fixture()
    response["total_source_chars"] += 1
    with pytest.raises(ValueError, match="Character"):
        audit.verify_response(response, {"parent": full}, {"a.py": full["text"]}, BytesCounter())
    _, response = fixture()
    with pytest.raises(ValueError, match="Character"):
        audit.verify_response(response, {"parent": full}, {"a.py": full["text"]}, BytesCounter(), chars=2)


def test_backfill_uses_actual_single_witness_preserves_old_metadata_and_new_origins():
    old = {"id": "child", "path": "a", "start_line": 2, "end_line": 3,
           "source": "two\nthree\n", "origin": "retrieval", "related_to": None}
    parent = {"id": "parent", "path": "a", "start_line": 1, "end_line": 4,
              "source": "one\ntwo\nthree\nfour\n", "origin": "retrieval", "related_to": None}
    audit.verify_preservation({"results": [old]}, {"results": [parent]})
    with pytest.raises(ValueError, match="old actual span"):
        audit.verify_preservation({"results": [old]}, {"results": [dict(parent, end_line=2, source="one\ntwo\n")]})
    with pytest.raises(ValueError, match="retained old row"):
        audit.verify_preservation({"results": [old]}, {"results": [dict(old, extra="metadata changed")]})
    with pytest.raises(ValueError, match="retrieval origin"):
        audit.verify_preservation({"results": [old]}, {"results": [dict(parent, origin="dependency", related_to="child")]})
    with pytest.raises(ValueError, match="old actual span"):
        audit.verify_preservation({"results": [old]}, {"results": [
            dict(parent, end_line=2, source="one\ntwo\n"), dict(parent, id="part2", start_line=3, source="three\nfour\n")]})


def test_group_loss_on_incomplete_query_and_dependency_attribution_stays_visible():
    groups = [[{"path": "a", "evidence_lines": [1, 2]}], [{"path": "b", "evidence_lines": [1, 2]}]]
    old = {"results": [{"id": "a", "path": "a", "start_line": 1, "end_line": 2,
                         "origin": "dependency", "related_to": "root"}]}
    before, after, gains, losses = audit.group_changes(old, {"results": []}, groups)
    assert before == [1, None] and after == [None, None] and not gains
    assert losses[0]["baseline_dependency_only"] is True and losses[0]["group"] == 1


def test_clean_subprocess_rejects_producer_model_imports_and_never_needs_network():
    code = '''
import importlib.util,sys
from pathlib import Path
p=Path("scripts/audit_selection.py")
s=importlib.util.spec_from_file_location("audit",p);a=importlib.util.module_from_spec(s);s.loader.exec_module(a)
sys.meta_path.insert(0,a.RefuseProduction())
for name in ("semantic_search.selection","semantic_search.context_backfill","replay_selection","numpy","mlx.core"):
 try:__import__(name)
 except RuntimeError:pass
 else:raise AssertionError(name)
print("independent import boundary verified")
'''
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "boundary verified" in result.stdout
