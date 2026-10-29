"""Protect paired accounting and failure handling in the new benchmark."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location('agent_grep_benchmark_test', SCRIPTS / 'benchmark_agent_grep.py')
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


def row(condition, **changes):
    result = {'task_id': 'task', 'repetition': 1, 'condition': condition,
              'completed': True, 'agent_exit_code': 0, 'failed': False, 'timed_out': False,
              'correct': True, 'completed_correct': True, 'base_tree_sha256': 'tree',
              'prompt_sha256': 'prompt', 'timing_qualified': True, 'agent_elapsed_s': 10,
              'service_setup_s': 0 if condition == 'baseline' else 5,
              'full_trial_elapsed_s': 15, 'usage': {'total_tokens': 100,
              'uncached_input_tokens': 70, 'cached_input_tokens': 20, 'output_tokens': 10}}
    result.update(changes)
    return result


def planned():
    return [{'task_id': 'task', 'repetition': 1, 'condition': c} for c in ('baseline', 'mcp')]


def test_missing_usage_is_not_zero_or_complete_total():
    result = benchmark.summarize([row('baseline'), row('mcp', usage=None)], planned())
    assert result['inventory_complete']
    assert result['conditions']['mcp']['full_inventory_tokens'] is None
    metric = result['comparisons']['all_pairs']['measurements']['total_tokens']
    assert metric['pairs'] == 0 and metric['mcp_sum'] is None and metric['change_pct'] is None


def test_failed_attempt_stays_in_inventory_but_not_correct_subset():
    records = [row('baseline'), row('mcp', timed_out=True, completed=False, completed_correct=False)]
    result = benchmark.summarize(records, planned())
    assert result['recorded_attempts'] == 2
    assert result['conditions']['mcp']['timeouts'] == 1
    assert result['comparisons']['all_pairs']['pairs'] == 1
    assert result['comparisons']['normally_completed_pairs']['pairs'] == 0
    assert result['comparisons']['both_correct_pairs']['pairs'] == 0


def test_incomplete_inventory_does_not_claim_full_total():
    result = benchmark.summarize([row('baseline')], planned())
    assert not result['inventory_complete']
    assert result['conditions']['mcp']['full_inventory_tokens'] is None


@pytest.mark.parametrize('field', ['prompt_sha256', 'base_tree_sha256'])
def test_mismatched_pair_rejected(field):
    with pytest.raises(ValueError, match='Paired fixtures/prompts differ'):
        benchmark.summarize([row('baseline'), row('mcp', **{field: 'different'})], planned())


def test_duplicate_attempt_rejected():
    with pytest.raises(ValueError, match='Duplicate or unscheduled'):
        benchmark.summarize([row('baseline'), row('baseline')], planned())


def test_qualified_time_and_token_subsets_remain_separate():
    result = benchmark.summarize([row('baseline'), row('mcp', timing_qualified=False)], planned())
    metrics = result['comparisons']['all_pairs']['measurements']
    assert metrics['agent_elapsed_s']['pairs'] == 0
    assert metrics['total_tokens']['baseline_sum'] == 100
    assert metrics['uncached_input_tokens']['baseline_sum'] == 70


def test_baseline_without_search_receipt(tmp_path):
    raw, public = tmp_path / 'raw', tmp_path / 'public'
    raw.mkdir()
    public.mkdir()
    result = row('baseline')
    benchmark.retain_receipts(result, raw, public)
    assert result['verified_dense_searches'] == 0 and result['http_searches'] == 0


def test_invalid_source_receipt_does_not_count_as_verified(tmp_path):
    raw, public = tmp_path / 'raw', tmp_path / 'public'
    raw.mkdir()
    public.mkdir()
    valid = {'settings': {}, 'response': {'retrieval': 'dense', 'rerank': False, 'results': [{}]},
             'returned_source_checks': [{'source_matches_snapshot': True,
                                        'physical_bounds_match': True, 'source_hash_matches': True}]}
    invalid = deepcopy(valid)
    invalid['returned_source_checks'][0]['source_matches_snapshot'] = False
    (raw / 'http-receipts.jsonl').write_text('\n'.join(json.dumps(r) for r in (valid, invalid)))
    result = row('mcp')
    benchmark.retain_receipts(result, raw, public)
    assert result['verified_dense_searches'] == 1
    assert result['successful_http_searches'] == 2
