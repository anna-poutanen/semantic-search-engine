#!/usr/bin/env python3
"""Offline posthoc verification of retained agent/grep evidence; never runs trials.

This standard-library verifier reads published data, not the current model/runtime.
It verifies saved evidence and recomputes accounting. It cannot certify when local
hash records were created or retroactively create a prospective Git freeze.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import re
import statistics
import tarfile
import tokenize

FIELDS = ('total_tokens', 'uncached_input_tokens', 'cached_input_tokens', 'output_tokens',
          'agent_elapsed_s', 'service_setup_s', 'full_trial_elapsed_s')
USAGE = ('input_tokens', 'cached_input_tokens', 'cache_write_input_tokens',
         'output_tokens', 'reasoning_output_tokens')
CONDITIONS = ('baseline', 'mcp')
COUNTS = ('passed', 'failed', 'skipped', 'xfailed', 'xpassed', 'deselected', 'errors')


def require(ok, message):
    if not ok:
        raise ValueError(message)


def read(path):
    return json.loads(path.read_text())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def lines(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def bounded(root, name):
    target = (root / name).resolve()
    require(target.is_relative_to(root.resolve()) and target.is_file(), 'Invalid artifact path: ' + name)
    return target


def normal(row):
    return bool(row.get('completed') and row.get('agent_exit_code') == 0 and not row.get('failed')
                and not row.get('timed_out') and not row.get('infrastructure_error') and not row.get('interrupted'))


def provider_usage(events):
    turns = [event.get('usage') for event in events if event.get('type') == 'turn.completed']
    if not turns:
        return None, 0
    require(all(isinstance(turn, dict) for turn in turns), 'Malformed completed-turn usage')
    for turn in turns:
        for key in USAGE:
            require(turn.get(key) is None or type(turn[key]) is int and turn[key] >= 0, 'Invalid usage count')
        for subset, total in (('cached_input_tokens', 'input_tokens'), ('cache_write_input_tokens', 'input_tokens'),
                              ('reasoning_output_tokens', 'output_tokens')):
            if turn.get(subset) is not None and turn.get(total) is not None:
                require(turn[subset] <= turn[total], 'Usage subset exceeds total')
    result = {key: sum(turn[key] for turn in turns) if all(type(turn.get(key)) is int for turn in turns)
              else None for key in USAGE}
    a, b, c = result['input_tokens'], result['output_tokens'], result['cached_input_tokens']
    result['total_tokens'] = a + b if a is not None and b is not None else None
    result['uncached_input_tokens'] = a - c if a is not None and c is not None else None
    return result, len(turns)


def qualified(row):
    if row.get('timing_qualified') is False or row.get('interrupted') or not row.get('cleanup_clean'):
        return False
    if row.get('clock_end', {}).get('consistent') is not True:
        return False
    a, b = row.get('agent_start'), row.get('agent_end')
    if not a or not b:
        return False
    wall, perf = b['wall_time_ns'] - a['wall_time_ns'], b['perf_counter_ns'] - a['perf_counter_ns']
    return wall >= 0 and perf >= 0 and abs(wall - perf) <= 2_000_000_000


def measured(row, field):
    if field.endswith('_s'):
        return row.get(field) if row.get('timing_qualified') is True else None
    return (row.get('usage') or {}).get(field)


def summarize(records, schedule):
    identities = [(r['task_id'], r['repetition'], r['condition']) for r in records]
    planned = {(r['task_id'], r['repetition'], r['condition']) for r in schedule}
    require(len(set(identities)) == len(records) and set(identities) <= planned, 'Duplicate or unscheduled attempt')
    result = {'planned_attempts': len(schedule), 'recorded_attempts': len(records),
              'inventory_complete': set(identities) == planned, 'conditions': {}, 'comparisons': {}}
    for condition in CONDITIONS:
        rows = [r for r in records if r['condition'] == condition]
        expected = sum(r['condition'] == condition for r in schedule)
        metrics = {}
        for field in FIELDS:
            values = [value for r in rows if (value := measured(r, field)) is not None]
            metrics[field] = {'observations': len(values), 'sum': sum(values) if values else None,
                              'median': statistics.median(values) if values else None}
        result['conditions'][condition] = {
            'planned': expected, 'recorded': len(rows), 'normally_completed': sum(normal(r) for r in rows),
            'completed_correct': sum(r['completed_correct'] for r in rows),
            'correct_artifacts': sum(r['correct'] for r in rows),
            'timeouts': sum(r.get('timed_out', False) for r in rows),
            'infrastructure_failures': sum(bool(r.get('infrastructure_error')) for r in rows),
            'searches': sum(r.get('verified_dense_searches', 0) for r in rows), 'measurements': metrics,
            'full_inventory_tokens': metrics['total_tokens']['sum']
            if metrics['total_tokens']['observations'] == expected else None}
    grouped = {}
    for row in records:
        grouped.setdefault((row['task_id'], row['repetition']), {})[row['condition']] = row
    pairs = []
    for key, members in grouped.items():
        if set(members) == set(CONDITIONS):
            a, b = members['baseline'], members['mcp']
            require(all(a.get(k) and a[k] == b.get(k) for k in ('base_tree_sha256', 'prompt_sha256')),
                    'Mismatched paired fixtures or prompts')
            pairs.append((key, a, b))
    for label in ('all_pairs', 'normally_completed_pairs', 'both_correct_pairs'):
        selected = [(key, a, b) for key, a, b in pairs if label == 'all_pairs' or normal(a) and normal(b)
                    and (label != 'both_correct_pairs' or a['completed_correct'] and b['completed_correct'])]
        metrics = {}
        for field in FIELDS:
            values = [(key, x, y) for key, a, b in selected
                      if (x := measured(a, field)) is not None and (y := measured(b, field)) is not None]
            a, b = sum(x for _, x, _ in values), sum(y for _, _, y in values)
            metrics[field] = {'pairs': len(values), 'members': [list(key) for key, _, _ in values],
                              'baseline_sum': a if values else None, 'mcp_sum': b if values else None,
                              'change_pct': 100 * (b / a - 1) if values and a else None}
        result['comparisons'][label] = {'pairs': len(selected), 'measurements': metrics}
    return result


def compact(response):
    fields = ('path', 'symbol', 'start_line', 'end_line', 'source', 'source_hash', 'truncated')
    rows = [{**{k: row[k] for k in fields}, 'origin': row.get('origin', 'retrieval'),
             'related_to': row.get('related_to')} for row in response['results']]
    for row, original in zip(rows, response['results'], strict=True):
        row.update({key: original[key] for key in ('source_origin', 'buffer_version', 'buffer_hash') if key in original})
    result = {'query': response['query'], 'match_status': response.get('match_status', 'candidates'),
              'results': rows, 'budget_exhausted': response.get('budget_exhausted', False),
              'token_usage': response['token_usage']}
    if 'scope' in response:
        result['scope'] = response['scope']
    return json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


def source_checks(public, receipts, calls):
    payloads = Counter()
    for call in calls:
        result = call.get('result') or {}
        content = result.get('content', [])
        if call.get('status') == 'completed' and not call.get('error'):
            require(len(content) == 1 and content[0].get('type') == 'text'
                    and result.get('structured_content') is None and result.get('structuredContent') is None,
                    'Search tool response is not single-copy text')
            payloads[hashlib.sha256(content[0]['text'].encode()).hexdigest()] += 1
    checked, verified = 0, 0
    if not receipts:
        require(not payloads, 'MCP payload has no HTTP receipt')
        return checked, verified
    with tarfile.open(public / 'source-snapshots.tar.gz') as archive:
        for receipt in receipts:
            response = receipt.get('response')
            require(response is not None and not receipt.get('snapshot_error'), 'Unverified search response')
            payload = hashlib.sha256(compact(response).encode()).hexdigest()
            require(payload == receipt['compact_text_sha256'] and payloads[payload] > 0, 'Receipt does not match actual MCP payload')
            payloads[payload] -= 1
            require(response['retrieval'] == 'dense' and response['rerank'] is False, 'Search overrides frozen method')
            for row in response['results']:
                name = f'snapshots/{receipt["number"]:04d}/after/{row["path"]}'
                stream = archive.extractfile(name)
                require(stream is not None, 'Missing source snapshot')
                saved = stream.read(); codec, _ = tokenize.detect_encoding(io.BytesIO(saved).readline)
                physical = [line for line in re.findall(r'[^\r\n]*(?:\r\n|\r|\n|$)', saved.decode(codec)) if line]
                start, end = row['start_line'], row['end_line']
                require(1 <= start <= end <= len(physical), 'Invalid physical source bounds')
                source = ''.join(physical[start - 1:end])
                require(source == row['source'] and hashlib.sha256(source.encode()).hexdigest() == row['source_hash'],
                        'Source excerpt differs from saved bytes')
                checked += 1
            verified += 1
    require(not any(payloads.values()), 'MCP payload omitted from HTTP inventory')
    return checked, verified


def pytest_counts(output):
    candidates = [line for line in output.splitlines() if re.search(r'\b\d+ (?:passed|failed|skipped|xfailed|xpassed|deselected|errors?)\b', line)]
    if not candidates:
        return None
    counts = dict.fromkeys(COUNTS, 0)
    for value, key in re.findall(r'\b(\d+) (passed|failed|skipped|xfailed|xpassed|deselected|errors?)\b', candidates[-1]):
        counts['errors' if key in ('error', 'errors') else key] += int(value)
    return counts


def audit(root, study, review):
    inputs = read(review / 'input-manifest.json')
    config, tasks, records = read(study / 'frozen-config.json'), read(study / 'tasks.json'), read(study / 'results.json')
    require(not inputs['unrecovered'], 'Frozen input recovery incomplete')
    for name, expected in config['artifact_sha256'].items():
        item = inputs['frozen_inputs'][name]
        require(item['sha256'] == expected and digest(bounded(review, item['path'])) == expected,
                'Frozen input bytes changed: ' + name)
    for item in [*inputs['frozen_inputs'].values(), *inputs['earlier_preflight']]:
        require(digest(bounded(review, item['path'])) == item['sha256'], 'Recovered input hash mismatch')
    inventory = read(review / 'original-inventory.json')
    for name, expected in inventory.items():
        require(digest(bounded(root, name)) == expected, 'Original historical artifact changed: ' + name)
    require(read(study / 'run-start.json')['config_sha256'] == digest(study / 'frozen-config.json'), 'Run-start binding changed')
    completion = read(study / 'completion.json')
    require(completion['inventory_complete'] and len(records) == len(config['schedule']) == completion['recorded_attempts'] == 16,
            'Attempt inventory incomplete')
    task_map = {task['id']: task for task in tasks['tasks']}
    require(digest(study / 'tasks.json') == config['manifest_sha256'], 'Task snapshot changed')
    for segment_file in sorted(study.glob('continuation-v[12].json')):
        segment = read(segment_file); archive = study / segment['prior_segment_directory']
        require(segment['frozen_config_sha256'] == digest(study / 'frozen-config.json')
                and segment['interrupted_attempts_retried'] is False, 'Continuation conditions changed')
        require(segment['continuation_driver_sha256'] == inputs['frozen_inputs']['scripts/continue_agent_grep.py']['sha256'],
                'Continuation driver binding differs')
        probe = read(study.parent / Path(segment['fresh_transport']).parent.name / 'result.json')
        probe_file = study.parent / Path(segment['fresh_transport']).parent.name / 'result.json'
        require(digest(probe_file) == segment['fresh_transport_sha256'] and probe['completed_correct'] is True
                and probe['common_runtime_sha256'] == config['common_runtime_sha256'], 'Continuation transport differs')
        for name, expected in segment['prior_artifact_sha256'].items():
            require(digest(bounded(archive, name)) == expected, 'Prior segment archive changed')
        require(records[:segment['prefix_attempts']] == read(archive / 'results.json')
                and segment['remaining_schedule'] == config['schedule'][segment['prefix_attempts']:], 'Continuation rewrote schedule or outcomes')
    witnesses, excerpts, searches = [], 0, 0
    for number, row in enumerate(records, 1):
        trial = config['schedule'][number - 1]
        require(all(row[k] == trial[k] for k in ('task_id', 'condition', 'repetition')), 'Attempt order changed')
        require(row['run_id'] == f'{number:02d}-r{row["repetition"]}-{row["task_id"]}-{row["condition"]}', 'Attempt identity differs')
        public = study / 'runs' / row['run_id']
        require(read(public / 'result.json') == row, 'Per-run outcome differs')
        for name, expected in row['artifact_sha256']['published'].items():
            require(digest(bounded(public, name)) == expected, 'Published raw evidence changed')
        require(digest(public / 'changes.patch') == row['patch_sha256'], 'Patch binding differs')
        events = lines(public / 'events.jsonl'); usage, turns = provider_usage(events)
        require(usage == row['usage'] and turns == row['reported_turns'], 'Usage differs from completed provider events')
        calls = [e['item'] for e in events if e.get('type') == 'item.completed' and e.get('item', {}).get('type') == 'mcp_tool_call']
        require(len(calls) == len({c['id'] for c in calls}) == row['mcp_calls'], 'MCP inventory differs')
        receipts = lines(public / 'http-receipts.jsonl') if (public / 'http-receipts.jsonl').exists() else []
        require(len(receipts) == row['http_searches'], 'Search receipt inventory differs')
        count, verified = source_checks(public, receipts, calls)
        require(verified == row['verified_dense_searches'], 'Verified search count differs')
        if row['condition'] == 'baseline':
            require(not calls and not receipts, 'Baseline has semantic search')
        task = task_map[row['task_id']]; grade = row['grade']; compatibility = row['compatibility']
        behavior = grade['exit_code'] == 0 and grade['detail'] == task['seed_validation']['reference']
        commands = compatibility['commands']
        compatible = len(commands) == len(task['compatibility_commands']) and all(
            actual['exit_code'] == 0 and actual['actual_summary'] == expected['expected_summary']
            and pytest_counts(actual['stdout']) == expected['expected_summary']
            for actual, expected in zip(commands, task['compatibility_commands'], strict=True))
        require(behavior == grade['passed'] and compatible == compatibility['passed']
                and row['correct'] == (behavior and compatible)
                and row['completed_correct'] == (behavior and compatible and normal(row)), 'Frozen correctness differs')
        require(row['prompt_sha256'] == config['prompt_sha256'][row['task_id']], 'Frozen prompt binding differs')
        excerpts += count; searches += verified
        witnesses.append({'run_id': row['run_id'], 'normal': normal(row), 'completed_correct': row['completed_correct'],
                          'interrupted': row.get('interrupted', False), 'usage': usage,
                          'timing_qualified': qualified(row), 'verified_dense_searches': verified})
    require(summarize(records, config['schedule']) == read(study / 'summary.json'), 'Producer summary differs')
    qualified_records = deepcopy(records)
    for row in qualified_records:
        row['timing_qualified'] = qualified(row)
    summary = summarize(qualified_records, config['schedule'])
    require(summary == read(study / 'qualified-summary.json'), 'Qualified summary differs')
    saved = read(study / 'qualified-measurements.json')
    require(len(saved) == len(records) and all(a['run_id'] == b['run_id'] and a['timing_qualified'] == b['timing_qualified']
            for a, b in zip(saved, qualified_records, strict=True)), 'Timing qualifications differ')
    patch_review = read(study / 'patch-review.json'); supplemental = read(study / 'supplemental-compatibility.json')
    indexed = {row['run_id']: row for row in records}
    require(patch_review['review_complete'] and {r['run_id'] for r in patch_review['reviewed_runs']} == set(indexed)
            and len(patch_review['reviewed_runs']) == len(records), 'Patch review inventory incomplete')
    for row in patch_review['reviewed_runs']:
        require(row['retained_patch_sha256'] == indexed[row['run_id']]['patch_sha256'], 'Review patch hash differs')
    require(supplemental['probe_source_sha256'] == inputs['frozen_inputs']['scripts/probe_agent_grep_compatibility.py']['sha256']
            and supplemental['upstream']['output'] == supplemental['reference']['output'], 'Supplemental control binding differs')
    rejected = []
    for probe in supplemental['results']:
        require(probe['retained_patch_sha256'] == indexed[probe['run_id']]['patch_sha256'], 'Probe patch binding differs')
        preserves = probe['observed']['exit_code'] == 0 and probe['observed']['output'] == supplemental['upstream']['output']
        require(preserves == probe['preserves_upstream'], 'Supplemental classification differs')
        if not preserves:
            rejected.append(probe['run_id'])
    require(rejected == supplemental['findings'], 'Supplemental defect inventory differs')
    adjusted = deepcopy(qualified_records)
    for row in adjusted:
        if row['run_id'] in rejected:
            row['correct'] = row['completed_correct'] = False
    comparison = summarize(adjusted, config['schedule'])['comparisons']['both_correct_pairs']
    old = read(study / 'evidence-audit.json')
    require(old['auditor_sha256'] == inputs['frozen_inputs']['scripts/report_agent_grep.py']['sha256'],
            'Historical reporter source binding differs')
    require(comparison == old['supplemental_compatibility']['review_adjusted_comparison']
            and excerpts == old['source_excerpts_checked'], 'Historical report differs from independent accounting')
    return {'classification': 'Independent posthoc offline evidence review; no trial rerun or retrospective Git freeze',
            'checks_passed': True, 'reviewer_sha256': digest(Path(__file__)), 'attempts': witnesses,
            'original_artifacts_verified': len(inventory), 'frozen_inputs_verified': len(config['artifact_sha256']),
            'source_excerpts_verified': excerpts, 'actual_mcp_payloads_matched': searches,
            'qualified_summary': summary, 'supplemental_known_regressions': rejected,
            'review_adjusted_comparison': comparison,
            'limits': ['Local freeze/hash/timestamp records have no prospective Git commit or external timestamp attestation.',
                       'Verification checks retained logs and literal source, not independent re-execution of coding or grader outcomes.',
                       'No new models, hosted agents, runtime package checks, latency measurements or billing claims.',
                       'Both interrupted attempts have unknown usage; five review-adjusted pairs are a selected post-output subset.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--study', type=Path, required=True)
    parser.add_argument('--review', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    require(not args.output.exists(), 'Choose a fresh review output; historical receipts cannot be overwritten')
    result = audit(Path(__file__).resolve().parents[1], args.study.resolve(), args.review.resolve())
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    print(json.dumps({k: result[k] for k in ('checks_passed', 'original_artifacts_verified', 'frozen_inputs_verified',
                                            'source_excerpts_verified', 'actual_mcp_payloads_matched')}))


if __name__ == '__main__':
    main()
