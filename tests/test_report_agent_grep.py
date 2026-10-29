"""Evidence checks must reject stale source and changed search payloads."""
import hashlib
import importlib.util
import io
from pathlib import Path
import sys
import tarfile

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location('agent_grep_report_test', SCRIPTS / 'report_agent_grep.py')
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)


def source_receipt():
    # Reuse the actual zero-model transport schema as a fixture, not its scores.
    from semantic_search.backend import SearchBackend
    from semantic_search.context import compact_result, serialize_context
    import tempfile
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / 'src').mkdir()
        (root / 'src/example.py').write_text('def add(a, b):\n    return a + b\n')
        backend = SearchBackend(root, 'src')
        try:
            response = backend.search('add', retrieval='bm25', max_results=1, token_budget=512)
        finally:
            backend.close()
    assert response['results']
    return {'number': 1, 'response': response,
            'compact_text_sha256': hashlib.sha256(serialize_context(compact_result(response)).encode()).hexdigest()}


def archive_source(tmp_path, source):
    with tarfile.open(tmp_path / 'source-snapshots.tar.gz', 'w:gz') as archive:
        raw = source.encode()
        item = tarfile.TarInfo('snapshots/0001/after/src/example.py')
        item.size = len(raw)
        archive.addfile(item, io.BytesIO(raw))


def test_literal_source_snapshot_passes(tmp_path):
    receipt = source_receipt()
    archive_source(tmp_path, 'def add(a, b):\n    return a + b\n')
    assert report.snapshot_checks(tmp_path, [receipt]) == 1


def test_changed_snapshot_is_rejected(tmp_path):
    receipt = source_receipt()
    archive_source(tmp_path, 'def add(a, b):\n    return a - b\n')
    with pytest.raises(ValueError, match='Delivered excerpt differs'):
        report.snapshot_checks(tmp_path, [receipt])


def test_changed_payload_is_rejected(tmp_path):
    receipt = source_receipt()
    receipt['compact_text_sha256'] = '0' * 64
    archive_source(tmp_path, 'def add(a, b):\n    return a + b\n')
    with pytest.raises(ValueError, match='Search response differs'):
        report.snapshot_checks(tmp_path, [receipt])


def timed_row():
    from qwen_optimization_clock import utc_from_ns
    def sample(value):
        return {'wall_time_ns': value, 'perf_counter_ns': value, 'utc': utc_from_ns(value)}
    return {'agent_start': sample(1_000_000_000), 'agent_end': sample(11_000_000_000),
            'clock_end': {'consistent': True}, 'cleanup_clean': True, 'interrupted': False}


def test_missing_positive_timing_marker_can_be_derived_without_mutating_raw():
    row = timed_row()
    assert report.qualify_timing(row)['timing_qualified'] is True
    assert 'timing_qualified' not in row


def test_explicit_unqualified_timing_is_never_promoted():
    row = timed_row()
    row['timing_qualified'] = False
    assert report.qualify_timing(row)['timing_qualified'] is False


@pytest.mark.parametrize('changes', [{'cleanup_clean': False}, {'interrupted': True},
                                   {'clock_end': {'consistent': False}}])
def test_timing_requires_clean_lifecycle_and_consistent_clocks(changes):
    row = timed_row()
    row.update(changes)
    assert report.qualify_timing(row)['timing_qualified'] is False
