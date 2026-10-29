"""An interrupted study may append unattempted IDs, never retry old ones."""
import importlib.util
import json
from pathlib import Path
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location('agent_grep_continuation_test', SCRIPTS / 'continue_agent_grep.py')
continuation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(continuation)


def fixture(tmp_path, monkeypatch):
    study = tmp_path / 'study'
    study.mkdir()
    work = tmp_path / 'work'
    previous = {'task_id': 'task', 'repetition': 1, 'condition': 'baseline'}
    pending = {'task_id': 'task', 'repetition': 1, 'condition': 'mcp'}
    config = {'common_runtime_sha256': 'runtime', 'schedule': [previous, pending], 'work_root': str(work)}
    (study / 'frozen-config.json').write_text(json.dumps(config))
    (study / 'results.json').write_text(json.dumps([{**previous, 'interrupted': True}]))
    (study / 'summary.json').write_text('{}')
    (study / 'completion.json').write_text('{}')
    probe = tmp_path / 'probe.json'
    probe.write_text(json.dumps({'completed_correct': True, 'common_runtime_sha256': 'runtime'}))
    monkeypatch.setattr(continuation.mf, 'verify_runtime', lambda _: None)
    return study, work, probe


@pytest.mark.parametrize('location', ['public', 'raw'])
def test_preexisting_attempt_identity_cannot_run_again(tmp_path, monkeypatch, location):
    study, work, probe = fixture(tmp_path, monkeypatch)
    root = study / 'runs' if location == 'public' else work / 'raw'
    (root / '02-r1-task-mcp').mkdir(parents=True)
    with pytest.raises(ValueError, match='already-attempted identity'):
        continuation.freeze(study, study / 'continuation-v1.json', probe)


def test_only_unattempted_suffix_is_frozen_and_old_results_preserved(tmp_path, monkeypatch):
    study, work, probe = fixture(tmp_path, monkeypatch)
    old_bytes = (study / 'results.json').read_bytes()
    target = study / 'continuation-v1.json'
    continuation.freeze(study, target, probe)
    plan = json.loads(target.read_text())
    assert plan['prefix_attempts'] == 1
    assert plan['remaining_schedule'] == [{'task_id': 'task', 'condition': 'mcp', 'repetition': 1}]
    assert plan['interrupted_attempts_retried'] is False
    assert (study / plan['prior_segment_directory'] / 'results.json').read_bytes() == old_bytes


def test_transport_from_another_runtime_is_rejected(tmp_path, monkeypatch):
    study, work, probe = fixture(tmp_path, monkeypatch)
    probe.write_text(json.dumps({'completed_correct': True, 'common_runtime_sha256': 'changed'}))
    with pytest.raises(ValueError, match='identical common runtime'):
        continuation.freeze(study, study / 'continuation-v1.json', probe)
