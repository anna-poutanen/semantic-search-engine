#!/usr/bin/env python3
"""Cross-check retained evidence and report the complete paired coding study."""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tarfile

import benchmark_agent_grep as benchmark
import qwen_workflow_evidence as usage
import run_multifile_workflows as mf
from qwen_optimization_clock import interval
from semantic_search.source import physical_lines


def require(ok, message):
    if not ok:
        raise ValueError(message)


def read(file):
    return json.loads(file.read_text())


def events(file):
    return [json.loads(line) for line in file.read_text().splitlines() if line.strip()]


def snapshot_checks(public, receipts):
    checked = 0
    if not receipts:
        return checked
    with tarfile.open(public / 'source-snapshots.tar.gz') as archive:
        for receipt in receipts:
            response = receipt.get('response')
            if response is None:
                continue
            compact = mf.serialize_context(mf.compact_result(response))
            require(hashlib.sha256(compact.encode()).hexdigest() == receipt['compact_text_sha256'],
                    'Search response differs from captured compact payload')
            require(not receipt.get('snapshot_error'), 'Search source snapshot verification failed')
            for row in response['results']:
                name = f'snapshots/{receipt["number"]:04d}/after/{row["path"]}'
                stream = archive.extractfile(name)
                require(stream is not None, 'Missing source snapshot')
                import io
                import tokenize
                saved = stream.read()
                codec, _ = tokenize.detect_encoding(io.BytesIO(saved).readline)
                text = saved.decode(codec)
                excerpt = ''.join(physical_lines(text)[row['start_line'] - 1:row['end_line']])
                require(excerpt == row['source'] and hashlib.sha256(excerpt.encode()).hexdigest()
                        == row['source_hash'], 'Delivered excerpt differs from saved source')
                checked += 1
    return checked


def qualify_timing(original):
    """Derive missing normal-run qualification from frozen clock/lifecycle evidence.

    The capture helper sets timing_qualified=False on interruption but does not
    set True on normal completion. Do not mutate producer records or its summary;
    apply the same timestamp/lifecycle rules to every run in a separate report.
    """
    row = deepcopy(original)
    a, b = row.get('agent_start'), row.get('agent_end')
    valid_interval = False
    if a and b:
        keys = ('wall_time_ns', 'perf_counter_ns', 'utc')
        witness = interval({key: a[key] for key in keys}, {key: b[key] for key in keys})
        valid_interval = witness['consistent']
        row['reported_agent_clock_interval'] = witness
    row['timing_qualified'] = bool(original.get('timing_qualified') is not False
        and row.get('clock_end', {}).get('consistent') is True
        and row.get('cleanup_clean') is True and not row.get('interrupted') and valid_interval)
    row['qualification_source'] = 'Frozen raw timestamps, 2-second drift bound, and clean owned-process lifecycle'
    return row


def audit(study, allow_partial=False):
    config, tasks = read(study / 'frozen-config.json'), read(study / 'tasks.json')
    records = read(study / 'results.json')
    mf.verify_runtime(config)
    require(tasks == read(benchmark.MANIFEST), 'Task contracts changed after freeze')
    require(read(study / 'run-start.json')['config_sha256'] == mf.old.digest(study / 'frozen-config.json'),
            'Frozen plan changed after first attempt')
    completion = read(study / 'completion.json') if (study / 'completion.json').exists() else None
    if not allow_partial:
        require(completion is not None and completion['inventory_complete'] is True
                and completion['recorded_attempts'] == len(config['schedule']) == len(records),
                'Final reporting requires the complete frozen inventory')
    continuation_witnesses = []
    for file in sorted(study.glob('continuation-v*.json')):
        segment = read(file)
        if 'remaining_schedule' not in segment:
            continue
        require(segment['frozen_config_sha256'] == mf.old.digest(study / 'frozen-config.json')
                and segment['continuation_driver_sha256'] == mf.old.digest(mf.ROOT / 'scripts/continue_agent_grep.py')
                and segment['interrupted_attempts_retried'] is False,
                'Continuation changed frozen conditions or retried an attempt')
        archive = study / segment['prior_segment_directory']
        for name, digest in segment['prior_artifact_sha256'].items():
            require(mf.old.digest(archive / name) == digest, 'Prior segment archive changed: ' + name)
        prior = read(archive / 'results.json')
        require(records[:segment['prefix_attempts']] == prior
                and segment['remaining_schedule'] == config['schedule'][segment['prefix_attempts']:],
                'Continuation changed previously observed outcomes or remaining schedule')
        continuation_witnesses.append({'segment': file.name, 'preserved_attempts': len(prior),
                                       'attempts_retried': 0})
    task_map = {task['id']: task for task in tasks['tasks']}
    witnesses, sources = [], 0
    for number, row in enumerate(records, 1):
        trial = config['schedule'][number - 1]
        require(all(row[key] == trial[key] for key in ('task_id', 'condition', 'repetition')), 'Frozen schedule differs')
        public = study / 'runs' / row['run_id']
        require(read(public / 'result.json') == row, 'Per-run and aggregate outcomes differ')
        items = events(public / 'events.jsonl')
        actual_usage = usage.provider_usage(items)
        require(row.get('usage') == actual_usage['usage']
                and row.get('reported_turns') == actual_usage['reported_turns'], 'Provider usage differs from retained events')
        calls = [item['item'] for item in items if item.get('type') == 'item.completed'
                 and item.get('item', {}).get('type') == 'mcp_tool_call']
        commands = [item['item'] for item in items if item.get('type') == 'item.completed'
                    and item.get('item', {}).get('type') == 'command_execution']
        require(len({call['id'] for call in calls}) == row.get('mcp_calls', 0), 'MCP call count differs')
        receipts_file = public / 'http-receipts.jsonl'
        receipts = events(receipts_file) if receipts_file.exists() else []
        require(len(receipts) == row['http_searches'], 'Independent search receipt count differs')
        if row['condition'] == 'baseline':
            require(not calls and not receipts, 'Baseline accessed the semantic search service')
        sources += snapshot_checks(public, receipts)
        task = task_map[row['task_id']]
        grade = row.get('grade', {})
        behavior = grade.get('exit_code') == 0 and grade.get('detail') == task['seed_validation']['reference']
        require(behavior == bool(grade.get('passed')), 'Frozen behavioral grade differs')
        compatibility = row.get('compatibility', {})
        commands_grade = compatibility.get('commands', [])
        compatible = len(commands_grade) == len(task['compatibility_commands']) and all(
            actual.get('exit_code') == 0 and actual.get('actual_summary') == expected['expected_summary']
            and mf.pytest_counts(actual.get('stdout', '')) == expected['expected_summary']
            for actual, expected in zip(commands_grade, task['compatibility_commands'], strict=True))
        require(compatible == bool(compatibility.get('passed')), 'Upstream compatibility grade differs')
        require(row['correct'] == (behavior and compatible), 'Correctness does not require both gates')
        require(row['completed_correct'] == (row['correct'] and benchmark.normal(row)),
                'Failed/interrupted agent incorrectly counted as completed-correct')
        require(row.get('prompt_sha256') == config['prompt_sha256'][row['task_id']], 'Unequal frozen task prompt')
        require(row.get('patch_sha256') == mf.old.digest(public / 'changes.patch'), 'Retained patch hash mismatch')
        require(row['clock_end']['consistent'] or row['timing_qualified'] is False, 'Unqualified timing was included')
        command_texts = [item.get('command', '') for item in commands]
        witnesses.append({'run_id': row['run_id'], 'behavior_passed': behavior, 'upstream_passed': compatible,
                          'completed_correct': row['completed_correct'], 'actual_search_calls': len(calls),
                          'verified_dense_searches': row['verified_dense_searches'], 'command_calls': len(commands),
                          'repository_search_commands': sum('rg ' in command or 'grep ' in command for command in command_texts),
                          'changed_original_tests_or_config': compatibility.get('changed_or_deleted_original_tests', []),
                          'usage': actual_usage['usage']})
    producer_summary = benchmark.summarize(records, config['schedule'])
    require(producer_summary == read(study / 'summary.json'), 'Aggregate measurements differ from recorded attempts')
    qualified_records = [qualify_timing(row) for row in records]
    summary = benchmark.summarize(qualified_records, config['schedule'])
    # Report paired correctness differences over every pair, including failures.
    members = {}
    for row in records:
        members.setdefault((row['task_id'], row['repetition']), {})[row['condition']] = row
    outcomes = Counter()
    for pair in members.values():
        if set(pair) == {'baseline', 'mcp'}:
            a, b = pair['baseline']['completed_correct'], pair['mcp']['completed_correct']
            outcomes['both_correct' if a and b else 'baseline_only' if a else 'mcp_only' if b else 'neither_correct'] += 1
    certificate = {'checks_passed': True, 'inventory_complete': summary['inventory_complete'],
                   'classification': 'Evidence cross-check by separate report code; not independent patch review',
                   'auditor_sha256': mf.old.digest(Path(__file__)), 'attempts': witnesses,
                   'source_excerpts_checked': sources, 'paired_correctness': dict(outcomes),
                   'continuation_segments': continuation_witnesses,
                   'interrupted_runs': [row['run_id'] for row in records if row.get('interrupted')],
                   'artifact_sha256': {name: mf.old.digest(study / name) for name in
                                      ('frozen-config.json', 'tasks.json', 'results.json', 'summary.json')},
                   'limits': ['Four authored tasks in observed repositories; eight paired attempts are descriptive.',
                              'Conditional search-first instructions measure prompted use, not spontaneous adoption.',
                              'Recorded usage is completed-turn provider usage, not per-request billing.',
                              'Frozen automated grades do not establish that all possible bugs are absent.']}
    certificate['timing_postprocessing'] = {
        'reason': 'Capture helper omits a True qualification marker on normal runs; producer raw records/summary remain unchanged',
        'policy': 'Derive from frozen clock consistency, within-agent wall/performance interval, clean lifecycle, no interruption; never promote explicit False',
        'qualified_runs': [row['run_id'] for row in qualified_records if row['timing_qualified']],
        'unqualified_runs': [row['run_id'] for row in qualified_records if not row['timing_qualified']]}
    diagnostics = []
    for name in ('agent-grep-transport-2026-10-15', 'agent-grep-transport-v2-2026-10-15',
                 'agent-grep-transport-v3-2026-10-16', 'agent-grep-transport-v4-2026-10-16'):
        file = study.parent / name / 'result.json'
        if file.exists():
            diagnostic = read(file)
            diagnostics.append({'directory': name, 'artifact_sha256': mf.old.digest(file),
                                'completed_correct': diagnostic.get('completed_correct'),
                                'agent_elapsed_s': diagnostic.get('agent_elapsed_s'),
                                'usage': diagnostic.get('usage')})
    certificate['separate_transport_diagnostics'] = diagnostics
    review_file = study / 'patch-review.json'
    if review_file.exists():
        review = read(review_file)
        reviewed = review['reviewed_runs']
        indexed = {row['run_id']: row for row in records}
        require(len({row['run_id'] for row in reviewed}) == len(reviewed), 'Duplicate patch review')
        for item in reviewed:
            require(item['run_id'] in indexed, 'Patch review names an unknown attempt')
            if 'retained_patch_sha256' in item:
                require(item['retained_patch_sha256'] == indexed[item['run_id']]['patch_sha256'],
                        'Manual review is not bound to the retained patch')
        complete_review = set(item['run_id'] for item in reviewed) == set(indexed) and review.get('review_complete') is True
        if not allow_partial:
            require(complete_review, 'Final reporting requires review of every retained patch/interruption')
        certificate['manual_patch_review'] = {
            'sha256': mf.old.digest(review_file), 'complete': complete_review,
            'reviewed_attempts': len(reviewed),
            'classification': review['classification'],
            'known_contract_regression_runs': [item['run_id'] for item in reviewed if item.get('findings')]}
    elif not allow_partial:
        raise ValueError('Final report requires the retained manual patch review')
    supplemental_file = study / 'supplemental-compatibility.json'
    if supplemental_file.exists():
        supplemental = read(supplemental_file)
        require(supplemental['probe_source_sha256'] == mf.old.digest(mf.ROOT / 'scripts/probe_agent_grep_compatibility.py')
                and supplemental['upstream']['output'] == supplemental['reference']['output'],
                'Supplemental compatibility control or source changed')
        indexed = {row['run_id']: row for row in records}
        for probe in supplemental['results']:
            require(probe['retained_patch_sha256'] == indexed[probe['run_id']]['patch_sha256'],
                    'Supplemental result not bound to retained candidate patch')
            require(probe['preserves_upstream'] == (probe['observed']['exit_code'] == 0
                    and probe['observed']['output'] == supplemental['upstream']['output']),
                    'Supplemental classification differs from observed control comparison')
        require(supplemental['findings'] == [row['run_id'] for row in supplemental['results']
                                           if not row['preserves_upstream']],
                'Supplemental regression inventory differs from individual probe outcomes')
        # Retain prospective frozen grades; add a clearly separate post-output view.
        rejected = set(supplemental['findings'])
        adjusted = deepcopy(qualified_records)
        for row in adjusted:
            if row['run_id'] in rejected:
                row['correct'] = row['completed_correct'] = False
        certificate['supplemental_compatibility'] = {
            'sha256': mf.old.digest(supplemental_file), 'known_contract_regressions': sorted(rejected),
            'classification': 'Post-output scoped regression probe; original automated grades retained',
            'review_adjusted_comparison': benchmark.summarize(adjusted, config['schedule'])['comparisons']['both_correct_pairs']}
    return qualified_records, summary, certificate


def fmt(number):
    if number is None:
        return 'unknown'
    return f'{number:,}' if type(number) is int else f'{number:,.2f}'


def findings(records, summary, audit):
    partial = not summary['inventory_complete']
    lines = ['# Fast semantic search versus ordinary repository tools' + (' — interim' if partial else ''), '',
             f'{len(records)}/16 attempts retained: four new maintenance tasks, two repetitions per condition. '
             'Both conditions use the same coding model, prompts, ordinary tools, time limit and grader. '
             'The search-assisted condition additionally receives the current local dense Qwen MCP tool.', '',
             '## Complete attempt inventory', '',
             '| Condition | Recorded / planned | Normally completed | Frozen completed-correct | Timeouts | Verified dense searches |',
             '|---|---:|---:|---:|---:|---:|']
    if summary['inventory_complete']:
        a, b = (summary['conditions'][name] for name in benchmark.CONDITIONS)
        conclusion = [
            'This run does not establish a coding-quality advantage for semantic search. '
            f'Ordinary tools completed {a["normally_completed"]}/{a["planned"]} attempts normally; '
            f'search-assisted tools completed {b["normally_completed"]}/{b["planned"]}. '
            'Sleep/connection interruptions are infrastructure outcomes, not method-quality wins.', '']
        extra = audit.get('supplemental_compatibility')
        if extra:
            regressions = extra['known_contract_regressions']
            conclusion += [f'Supplemental review confirmed {len(regressions)} scoped compatibility '
                           'regression in a search-assisted Click patch beyond the frozen grader. The original automated grades '
                           'and the separately labelled review-adjusted measurements both remain visible.', '']
            measured = extra['review_adjusted_comparison']
            conclusion += [f'Across {measured["pairs"]} matched pairs with no detected contract defect, '
                           'the changes below are descriptive; negative values mean search used less:', '',
                           '| Metric | Search versus ordinary tools |', '|---|---:|']
            for field in ('agent_elapsed_s', 'full_trial_elapsed_s', 'total_tokens', 'uncached_input_tokens'):
                metric = measured['measurements'][field]
                change = 'unknown' if metric['change_pct'] is None else f'{metric["change_pct"]:+.2f}%'
                conclusion.append(f'| {field} | {change} |')
            conclusion += ['', 'This is a small post-output subset of authored maintenance tasks, '
                           'not a representative coding benchmark or a billing-savings estimate.', '']
        # Insert the outcome before the full inventory rather than burying it below tables.
        lines[4:4] = conclusion
    for condition in benchmark.CONDITIONS:
        row = summary['conditions'][condition]
        lines.append(f'| {condition} | {row["recorded"]}/{row["planned"]} | {row["normally_completed"]} | '
                     f'{row["completed_correct"]} | {row["timeouts"]} | {row["searches"]} |')
    lines += ['', 'Frozen success requires passing the withheld behavior checks and restored upstream tests, '
              'plus normal CLI completion. Agent test-pass claims do not determine grades. Supplemental '
              'review can still find contract defects after these gates pass.', '',
              '| Attempt | Frozen success | Agent seconds | Setup seconds | Full trial seconds | Gross tokens | Uncached input |',
              '|---|---:|---:|---:|---:|---:|---:|']
    for row in records:
        lines.append(f'| {row["run_id"]} | {row["completed_correct"]} | '
                     + ' | '.join(fmt(benchmark.value(row, field)) for field in
                                  ('agent_elapsed_s', 'service_setup_s', 'full_trial_elapsed_s',
                                   'total_tokens', 'uncached_input_tokens')) + ' |')
    if audit['interrupted_runs']:
        lines += ['', '**Infrastructure interruption:** ' + ', '.join(audit['interrupted_runs']) +
                  ' stopped after sleep/connection gaps. Interrupted attempts were not retried. '
                  'Their timings are unqualified and incomplete usage is unknown. Remaining, unattempted '
                  'identities ran in separately frozen segments after successful transport probes. '
                  'These failures must not be attributed to the search method; cross-segment provider '
                  'variability is an additional limitation.', '']
    lines += ['', '## Task-level outcomes', '',
              'Each condition has two scheduled attempts per task. Mean times below use only '
              'matched, normally completed repetitions with qualified timings in both conditions. '
              'Unpaired observations and failures remain in the attempt table above.', '',
              '| Task | Baseline frozen pass / observed | Search frozen pass / observed | Baseline mean agent s | Search mean agent s |',
              '|---|---:|---:|---:|---:|']
    for task_id in dict.fromkeys(row['task_id'] for row in records):
        cells = []
        times = []
        repetitions = {}
        for row in records:
            if row['task_id'] == task_id:
                repetitions.setdefault(row['repetition'], {})[row['condition']] = row
        matched = {rep for rep, members in repetitions.items()
                   if set(members) == set(benchmark.CONDITIONS)
                   and all(benchmark.normal(row) and benchmark.value(row, 'agent_elapsed_s') is not None
                           for row in members.values())}
        for condition in benchmark.CONDITIONS:
            rows = [r for r in records if r['task_id'] == task_id and r['condition'] == condition]
            cells.append(f'{sum(r["completed_correct"] for r in rows)}/{len(rows)}')
            measured = [benchmark.value(r, 'agent_elapsed_s') for r in rows if r['repetition'] in matched]
            measured = [x for x in measured if x is not None]
            times.append(fmt(sum(measured) / len(measured)) if measured else 'unknown')
        lines.append('| ' + ' | '.join([task_id, *cells, *times]) + ' |')
    lines += ['', '## Paired correctness', '',
              'Each pair has identical starting source and task prompt. Balanced order reverses between repetitions.', '']
    for key in ('both_correct', 'baseline_only', 'mcp_only', 'neither_correct'):
        lines.append(f'- {key}: {audit["paired_correctness"].get(key, 0)}')
    if audit.get('supplemental_compatibility'):
        extra = audit['supplemental_compatibility']
        lines += ['', '## Supplemental contract regression', '',
                  'Manual patch review found an interaction missing from the frozen checker: '
                  'Click normalizes canonical names and then casefolds, and the two-stage collision '
                  'ordering is existing behavior even without aliases. A separate post-output probe '
                  'compares each completed Click candidate to both pinned upstream and the reference.', '',
                  'Known regressions: ' + (', '.join(extra['known_contract_regressions']) or 'none in checked candidates') + '.', '',
                  'For choices `A_B`, `a_b`, `A-B`, underscore-to-hyphen normalization and case-insensitive '
                  'conversion of `a_b`, upstream and reference return `a_b`. A fused per-choice '
                  'normalization/casefold loop can return `A-B` instead, violating the stated requirement '
                  'to preserve behavior without aliases. This does not rewrite frozen grades.', '',
                  'The additional review-adjusted comparison excludes pairs containing that known '
                  'contract regression. It is a post-output diagnostic, not a replacement primary grade. '
                  'Its contributing members and measurements are retained in evidence-audit.json.', '',
                  '| Review-adjusted metric | Pairs | Baseline sum | Search-assisted sum | Change |',
                  '|---|---:|---:|---:|---:|']
        for field in ('agent_elapsed_s', 'full_trial_elapsed_s', 'total_tokens', 'uncached_input_tokens'):
            metric = extra['review_adjusted_comparison']['measurements'][field]
            change = 'unknown' if metric['change_pct'] is None else f'{metric["change_pct"]:+.2f}%'
            lines.append(f'| {field} | {metric["pairs"]} | {fmt(metric["baseline_sum"])} | '
                         f'{fmt(metric["mcp_sum"])} | {change} |')
    lines += ['', '## Paired usage and time', '',
              'Changes are (search-assisted / baseline − 1), calculated from matched sums. '
              'Negative means less time or fewer tokens. Cached input is a subset of input; it is not added '
              'to gross tokens. Missing measurements remain unknown.', '']
    for label, values in summary['comparisons'].items():
        lines += [f'### {label}', '', f'Eligible pairs: {values["pairs"]}.', '',
                  '| Metric | Measured pairs | Baseline sum | Search-assisted sum | Change |',
                  '|---|---:|---:|---:|---:|']
        for field, metric in values['measurements'].items():
            percentage = 'unknown' if metric['change_pct'] is None else f'{metric["change_pct"]:+.2f}%'
            lines.append(f'| {field} | {metric["pairs"]} | {fmt(metric["baseline_sum"])} | '
                         f'{fmt(metric["mcp_sum"])} | {percentage} |')
        lines.append('')
    lines += ['## Evidence and interpretation limits', '',
              f'The evidence audit checked {audit["source_excerpts_checked"]} returned source excerpts against '
              'retained saved-source snapshots and reconstructed provider usage and aggregate grades. '
              'Per-run patches, commands, events, source receipts, grading logs and exact upstream test '
              'counts are retained. See `evidence-audit.json`, `qualified-summary.json` and `runs/`.', '',
              'The capture helper omitted a positive timing-qualification marker on normal runs. '
              'Raw producer records and summary.json remain unchanged. This report derives qualification '
              'uniformly from the already captured wall/performance clocks and clean process shutdown; '
              'qualified-measurements.json and qualified-summary.json retain that separate calculation.', '',
              'Model: gpt-6.1-sol, ultra effort, 900 seconds per attempt. Search setup precedes agent '
              'timing; full trial timing includes setup and grading. Comparisons retain failures and '
              'explicitly name measured pair members in qualified-summary.json.', '']
    if audit.get('separate_transport_diagnostics'):
        lines += ['Transport diagnostics are outside the 16 coding attempts and all paired totals:', '',
                  '| Diagnostic | Passed | Reported gross tokens | Agent seconds |',
                  '|---|---:|---:|---:|']
        for item in audit['separate_transport_diagnostics']:
            lines.append(f'| {item["directory"]} | {item["completed_correct"]} | '
                         f'{fmt((item.get("usage") or {}).get("total_tokens"))} | {fmt(item["agent_elapsed_s"])} |')
        lines.append('')
    lines.extend('- ' + limit for limit in audit['limits'])
    lines += ['- Prompt and filesystem/provider cache effects, task familiarity, and hosted variability limit causal attribution.',
              '- This measures realistic controlled maintenance scenarios, not a representative sample of production issues.',
              '- Supplemental patch review is recorded separately from frozen automated grades.', '']
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--study', type=Path, required=True)
    parser.add_argument('--allow-partial', action='store_true')
    args = parser.parse_args()
    records, summary, certificate = audit(args.study, args.allow_partial)
    mf.write_json(args.study / 'evidence-audit.json', certificate)
    mf.write_json(args.study / 'qualified-measurements.json', records)
    mf.write_json(args.study / 'qualified-summary.json', summary)
    (args.study / 'findings.md').write_text(findings(records, summary, certificate))
    print(json.dumps({'checks_passed': True, 'inventory_complete': certificate['inventory_complete'],
                      'attempts': len(records), 'paired_correctness': certificate['paired_correctness']}, indent=2))


if __name__ == '__main__':
    main()
