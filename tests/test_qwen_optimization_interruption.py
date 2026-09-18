"""CPU recovery tests: original bytes stay untouched, missing evidence stays absent."""
import gzip
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts/audit_qwen_optimization_interruption.py"
sys.path.insert(0, str(SCRIPT.parent))
spec = importlib.util.spec_from_file_location("interruption_auditor_tested", SCRIPT)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)
sys.path.pop(0)


def test_valid_gzip_records_exact_original_and_retained_bytes():
    payload = b'{"one":1}\n{"two":2}\n'
    original = gzip.compress(payload, mtime=0)
    rows, retained, info = audit.recovered_lines(original)
    assert rows == [{"one": 1}, {"two": 2}] and retained == payload
    assert info["gzip_footer_verified"] and info["decompression_error"] is None
    assert info["original_compressed_sha256"] == audit.sha(original)
    assert info["retained_exact_line_sha256"] == audit.sha(payload)
    assert info["unretained_decoded_bytes"] == 0


@pytest.mark.parametrize("missing_footer_bytes", [1, 4, 8])
def test_missing_footer_recovery_never_claims_original_closure(missing_footer_bytes):
    payload = '{"source":"héllo 🤖"}\n'.encode()
    original = gzip.compress(payload, mtime=0)[:-missing_footer_bytes]
    rows, retained, info = audit.recovered_lines(original)
    assert rows == [{"source": "héllo 🤖"}] and retained == payload
    assert not info["gzip_footer_verified"] and info["decompression_error"] is None
    assert gzip.decompress(gzip.compress(retained, mtime=0)) == payload
    assert original == gzip.compress(payload, mtime=0)[:-missing_footer_bytes]


@pytest.mark.parametrize("tail", [b'{"cut":', b'{"cut":"\xf0\x9f', b'{"complete_but_unterminated":true}'])
def test_incomplete_utf8_or_json_line_remains_unretained_and_hashed(tail):
    valid = b'{"kept":1}\n'
    rows, retained, info = audit.recovered_lines(gzip.compress(valid + tail, mtime=0)[:-8])
    assert rows == [{"kept": 1}] and retained == valid
    assert info["unretained_decoded_bytes"] == len(tail)
    assert info["unretained_decoded_sha256"] == audit.sha(tail)
    assert not info["gzip_footer_verified"]


@pytest.mark.parametrize("invalid", [b'broken\n', b'[]\n', b'\n', b'{"bad":"\xff"}\n'])
def test_first_invalid_complete_line_stops_recovery_instead_of_skipping_evidence(invalid):
    valid, later = b'{"kept":1}\n', b'{"not_reached":2}\n'
    rows, retained, info = audit.recovered_lines(gzip.compress(valid + invalid + later, mtime=0))
    assert rows == [{"kept": 1}] and retained == valid
    assert info["json_line_error"] is not None
    assert info["unretained_decoded_sha256"] == audit.sha(invalid + later)


def test_corrupt_crc_retains_prior_decoded_receipts_and_reports_error():
    payload = b'{"one":1}\n{"two":2}\n'
    original = bytearray(gzip.compress(payload, mtime=0))
    original[-8] ^= 0xFF
    rows, retained, info = audit.recovered_lines(bytes(original))
    assert rows == [{"one": 1}, {"two": 2}] and retained == payload
    assert not info["gzip_footer_verified"] and info["decompression_error"] is not None


def test_unexpected_concatenated_members_are_not_silently_certified():
    first, second = b'{"first":1}\n', b'{"second":2}\n'
    rows, retained, info = audit.recovered_lines(gzip.compress(first, mtime=0) + gzip.compress(second, mtime=0))
    assert rows == [{"first": 1}] and retained == first
    assert not info["gzip_footer_verified"] and info["compressed_unused_bytes"] > 0


def test_recovery_never_invents_a_json_row_when_no_complete_line_exists():
    rows, retained, info = audit.recovered_lines(gzip.compress(b'{"unfinished":1', mtime=0)[:-8])
    assert rows == [] and retained == b""
    assert info["retained_complete_json_lines"] == 0 and info["unretained_decoded_bytes"] > 0


def test_recorded_source_commit_mismatch_is_explicit_not_an_archive_failure(monkeypatch):
    committed = b"old committed source"
    frozen = b"final uncommitted clock patch"
    stable = b"stable source"
    plan = {"source_commit": "a" * 40, "implementation_sha256": {
        "scripts/pipeline.py": audit.sha(frozen), "scripts/stable.py": audit.sha(stable)}}

    def git(args, **kwargs):
        assert args[:4] == ["git", "-C", str(audit.ROOT), "show"]
        assert kwargs["capture_output"] and not kwargs["check"] and kwargs["timeout"] == 15
        return SimpleNamespace(returncode=0, stdout=committed if args[-1].endswith("pipeline.py") else stable)

    monkeypatch.setattr(audit.subprocess, "run", git)
    bindings, mismatch = audit.committed_source_bindings(plan)
    assert mismatch == ["scripts/pipeline.py"]
    assert bindings["scripts/pipeline.py"] == {"frozen_source_sha256": audit.sha(frozen),
        "committed_source_sha256": audit.sha(committed), "matches_recorded_commit": False}
    assert bindings["scripts/stable.py"]["matches_recorded_commit"]


def test_missing_file_in_recorded_commit_stays_a_missing_binding(monkeypatch):
    monkeypatch.setattr(audit.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=128, stdout=b""))
    bindings, mismatch = audit.committed_source_bindings({"source_commit": "a" * 40, "implementation_sha256": {"missing.py": "expected"}})
    assert mismatch == ["missing.py"] and bindings["missing.py"]["committed_source_sha256"] is None
