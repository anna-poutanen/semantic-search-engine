"""Posthoc evidence verification must reject plausible but unsupported accounting."""
from copy import deepcopy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('agent_grep_publication', ROOT / 'scripts/audit_agent_grep_publication.py')
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def test_all_retained_artifacts_reproduce_without_runtime_or_models():
    result = audit.audit(ROOT, ROOT / 'results/agent-grep-2026-10-15',
                         ROOT / 'results/agent-grep-publication-review-2026-10-29')
    assert result['checks_passed']
    assert len(result['attempts']) == 16
    assert result['original_artifacts_verified'] == 268
    assert result['frozen_inputs_verified'] == 64
    assert result['source_excerpts_verified'] == 61
    assert result['actual_mcp_payloads_matched'] == 14
    assert sum(row['usage'] is None for row in result['attempts']) == 2
    assert result['review_adjusted_comparison']['pairs'] == 5


def turn(**changes):
    value = dict(input_tokens=100, cached_input_tokens=70, cache_write_input_tokens=0,
                 output_tokens=20, reasoning_output_tokens=10)
    value.update(changes)
    return {'type': 'turn.completed', 'usage': value}


def test_missing_turn_usage_remains_unknown():
    assert audit.provider_usage([{'type': 'turn.failed'}]) == (None, 0)


def test_cached_tokens_are_not_added_to_total():
    result, count = audit.provider_usage([turn(), turn()])
    assert count == 2 and result['total_tokens'] == 240 and result['uncached_input_tokens'] == 60


@pytest.mark.parametrize('changes', [{'input_tokens': -1}, {'input_tokens': True}, {'cached_input_tokens': 101},
                                     {'output_tokens': '20'}, {'reasoning_output_tokens': 21}])
def test_invalid_provider_usage_is_rejected(changes):
    with pytest.raises(ValueError):
        audit.provider_usage([turn(**changes)])


def test_missing_field_does_not_become_zero():
    result, _ = audit.provider_usage([turn(cached_input_tokens=None)])
    assert result['total_tokens'] == 120 and result['uncached_input_tokens'] is None


def timed(**changes):
    row = {'agent_start': {'wall_time_ns': 1_000_000_000, 'perf_counter_ns': 1_000_000_000},
           'agent_end': {'wall_time_ns': 11_000_000_000, 'perf_counter_ns': 11_000_000_000},
           'cleanup_clean': True, 'clock_end': {'consistent': True}, 'interrupted': False}
    row.update(changes)
    return row


def test_missing_positive_timing_marker_uses_captured_interval():
    assert audit.qualified(timed())


@pytest.mark.parametrize('changes', [{'timing_qualified': False}, {'interrupted': True}, {'cleanup_clean': False},
                                     {'clock_end': {'consistent': False}}, {'agent_end': None}])
def test_unqualified_measurements_cannot_be_promoted(changes):
    assert not audit.qualified(timed(**changes))


def test_reported_consistency_cannot_hide_drift():
    row = timed()
    row['agent_end']['wall_time_ns'] += 2_000_000_001
    assert not audit.qualified(row)


def test_invalid_artifact_path_is_rejected(tmp_path):
    outside = tmp_path / 'outside'; outside.write_text('value')
    root = tmp_path / 'root'; root.mkdir()
    with pytest.raises(ValueError, match='Invalid artifact path'):
        audit.bounded(root, '../outside')


def evidence(tmp_path):
    source = 'def example():\r\n    return "a\u2028b"\r\n'
    row = {'path': 'src/example.py', 'symbol': 'example', 'start_line': 1, 'end_line': 2,
           'source': source, 'source_hash': hashlib.sha256(source.encode()).hexdigest(), 'truncated': False}
    response = {'query': 'example', 'results': [row], 'retrieval': 'dense', 'rerank': False,
                'token_usage': {'tokens': 50, 'budget': 100, 'encoding': 'cl100k_base', 'truncated': False}}
    payload = audit.compact(response)
    receipt = {'number': 1, 'response': response, 'compact_text_sha256': hashlib.sha256(payload.encode()).hexdigest()}
    call = {'status': 'completed', 'result': {'content': [{'type': 'text', 'text': payload}]}}
    with tarfile.open(tmp_path / 'source-snapshots.tar.gz', 'w:gz') as archive:
        info = tarfile.TarInfo('snapshots/0001/after/src/example.py'); info.size = len(source.encode())
        archive.addfile(info, io.BytesIO(source.encode()))
    return receipt, call


def test_actual_payload_and_literal_physical_lines_are_checked(tmp_path):
    receipt, call = evidence(tmp_path)
    assert audit.source_checks(tmp_path, [receipt], [call]) == (1, 1)


def test_self_consistent_receipt_without_actual_mcp_payload_is_rejected(tmp_path):
    receipt, call = evidence(tmp_path)
    receipt['response']['query'] = 'changed'
    receipt['compact_text_sha256'] = hashlib.sha256(audit.compact(receipt['response']).encode()).hexdigest()
    with pytest.raises(ValueError, match='actual MCP payload'):
        audit.source_checks(tmp_path, [receipt], [call])


def test_self_consistent_payload_cannot_forge_source_snapshot(tmp_path):
    receipt, call = evidence(tmp_path)
    row = receipt['response']['results'][0]; row['source'] = row['source'].replace('return', 'yield ')
    row['source_hash'] = hashlib.sha256(row['source'].encode()).hexdigest()
    payload = audit.compact(receipt['response'])
    receipt['compact_text_sha256'] = hashlib.sha256(payload.encode()).hexdigest()
    call['result']['content'][0]['text'] = payload
    with pytest.raises(ValueError, match='saved bytes'):
        audit.source_checks(tmp_path, [receipt], [call])


def test_omitted_actual_mcp_call_is_rejected(tmp_path):
    receipt, call = evidence(tmp_path)
    with pytest.raises(ValueError, match='omitted'):
        audit.source_checks(tmp_path, [receipt], [call, deepcopy(call)])


def test_duplicate_receipt_cannot_reuse_one_payload(tmp_path):
    receipt, call = evidence(tmp_path)
    with pytest.raises(ValueError, match='actual MCP payload'):
        audit.source_checks(tmp_path, [receipt, deepcopy(receipt)], [call])


def test_override_cannot_be_counted_as_frozen_dense_method(tmp_path):
    receipt, call = evidence(tmp_path)
    receipt['response']['rerank'] = True
    payload = audit.compact(receipt['response'])
    receipt['compact_text_sha256'] = hashlib.sha256(payload.encode()).hexdigest()
    call['result']['content'][0]['text'] = payload
    with pytest.raises(ValueError, match='overrides'):
        audit.source_checks(tmp_path, [receipt], [call])


def test_duplicate_structured_payload_is_rejected(tmp_path):
    receipt, call = evidence(tmp_path)
    call['result']['structured_content'] = receipt['response']
    with pytest.raises(ValueError, match='single-copy'):
        audit.source_checks(tmp_path, [receipt], [call])


def test_all_manifest_bound_inputs_are_tracked_in_git_checkout():
    # Disk-only audits miss files omitted by ignore rules during publication.
    # Keep this repository integration check separate from pure data fixtures.
    if shutil.which('git') is None:
        pytest.skip('Git is unavailable; byte-level fixture checks remain usable')
    probe = subprocess.run(['git', '-C', str(ROOT), 'rev-parse', '--show-toplevel'],
                           capture_output=True, text=True)
    if probe.returncode != 0 or Path(probe.stdout.strip()).resolve() != ROOT.resolve():
        pytest.skip('Source distribution without its own Git checkout')
    review = ROOT / 'results/agent-grep-publication-review-2026-10-29'
    manifest = json.loads((review / 'input-manifest.json').read_text())
    inputs = [*manifest['frozen_inputs'].values(), *manifest['earlier_preflight']]
    paths = []
    for item in inputs:
        file = audit.bounded(review, item['path'])
        paths.append(str(file.relative_to(ROOT)))
    tracked = subprocess.run(['git', '-C', str(ROOT), 'ls-files', '-z', '--', *paths],
                             capture_output=True, check=True).stdout.decode().split('\0')
    missing = sorted(set(paths) - set(tracked))
    assert not missing, f'Manifest-bound evidence omitted from Git: {missing}'
