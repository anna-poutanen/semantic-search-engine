#!/usr/bin/env python3
"""Freeze, run, audit, and report a paired fast-search coding-agent benchmark."""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import tarfile

import run_multifile_workflows as mf
import qwen_workflow_process as capture
from qwen_optimization_clock import ClockIntegrity, ClockIntegrityError

ROOT = mf.ROOT
MANIFEST = ROOT / 'benchmarks/agent_grep_tasks.json'
PROTOCOL = ROOT / 'docs/agent-grep-benchmark.md'
CONDITIONS = ('baseline', 'mcp')
INSTRUCTION = (
    'When search_code is available, use it before broad source reads to locate the relevant '
    'implementations. Use its fast default: dense retrieval without reranking. Use returned '
    'excerpts as source reads, then inspect missing definitions or ranges as needed. When it is '
    'unavailable, use ordinary repository search and reads, including grep or rg. Both paths '
    'may use all ordinary repository tools. Run focused regression tests and relevant existing '
    'tests; summarize failures with --tb=short instead of dumping large logs.'
)
ORIGINAL_PROMPT = mf.prompt


def prompt(task, python):
    return ORIGINAL_PROMPT(task, python).replace(mf.WORKFLOW_INSTRUCTION, INSTRUCTION)


def read(file):
    return json.loads(Path(file).read_text())


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha_text(text):
    return hashlib.sha256(text.encode()).hexdigest()


def freeze(output, work_root, common, probe):
    require(not output.exists() and not work_root.exists(), 'Choose fresh study and work paths')
    config, diagnostic, manifest = read(common), read(probe), read(MANIFEST)
    mf.verify_runtime(config)
    require(diagnostic.get('completed_correct') is True
            and diagnostic['common_runtime_sha256'] == mf.old.digest(common), 'Passing current transport probe required')
    require(len(manifest['tasks']) == 4, 'All four new tasks required')
    files = {str(MANIFEST.relative_to(ROOT)), str(PROTOCOL.relative_to(ROOT)),
             'scripts/benchmark_agent_grep.py', 'scripts/prepare_agent_grep.py',
             'benchmarks/agent_grep_checks.py', 'scripts/qwen_workflow_process.py',
             'scripts/qwen_workflow_evidence.py', 'scripts/qwen_optimization_clock.py'}
    for task in manifest['tasks']:
        require(mf.old.git(ROOT / task['corpus'], 'rev-parse', 'HEAD').stdout.decode().strip()
                == task['revision'], 'Pinned corpus revision changed')
        require(not mf.old.git(ROOT / task['corpus'], 'status', '--porcelain').stdout.strip(), 'Dirty source corpus')
        require(task['seed_validation']['reference']['passed'] is True
                and not task['seed_validation']['base']['passed'], 'Discriminating reference controls required')
        for name, digest in task['control_artifact_sha256'].items():
            require(mf.old.digest(ROOT / name) == digest, 'Control evidence changed: ' + name)
            files.add(name)
    config = deepcopy(config)
    config.update(classification='New four-task paired search-assisted versus ordinary-tools coding study',
                  manifest=str(MANIFEST.relative_to(ROOT)), manifest_sha256=mf.old.digest(MANIFEST),
                  common_runtime_sha256=mf.old.digest(common), probe_sha256=mf.old.digest(probe),
                  workflow_instruction=INSTRUCTION, work_root=str(work_root.resolve()),
                  schedule=mf.schedule(manifest['tasks']), planned_attempts=16,
                  prompt_sha256={task['id']: sha_text(prompt(task, config['python'])) for task in manifest['tasks']})
    config['artifact_sha256'].update({name: mf.old.digest(ROOT / name) for name in sorted(files)})
    config['mcp']['prompted_start'] = 'Product defaults; dense retrieval, no reranking'
    output.mkdir(parents=True)
    mf.write_json(output / 'tasks.json', manifest)
    mf.write_json(output / 'transport.json', diagnostic)
    mf.write_json(output / 'frozen-config.json', config)
    print('Frozen 16 attempts; no coding trials started', flush=True)


def stats(values):
    return {'observations': len(values), 'sum': sum(values) if values else None,
            'median': statistics.median(values) if values else None}


def value(row, field):
    if field.endswith('_s'):
        return row.get(field) if row.get('timing_qualified') is True else None
    return (row.get('usage') or {}).get(field)


FIELDS = ('total_tokens', 'uncached_input_tokens', 'cached_input_tokens', 'output_tokens',
          'agent_elapsed_s', 'service_setup_s', 'full_trial_elapsed_s')


def summarize(records, schedule):
    planned = {(r['task_id'], r['repetition'], r['condition']) for r in schedule}
    actual = [(r['task_id'], r['repetition'], r['condition']) for r in records]
    require(len(actual) == len(set(actual)) and set(actual) <= planned, 'Duplicate or unscheduled attempt')
    result = {'planned_attempts': len(schedule), 'recorded_attempts': len(records),
              'inventory_complete': set(actual) == planned, 'conditions': {}, 'comparisons': {}}
    for condition in CONDITIONS:
        rows = [row for row in records if row['condition'] == condition]
        expected = sum(row['condition'] == condition for row in schedule)
        measures = {field: stats([x for row in rows if (x := value(row, field)) is not None]) for field in FIELDS}
        result['conditions'][condition] = {
            'planned': expected, 'recorded': len(rows),
            'normally_completed': sum(normal(row) for row in rows),
            'completed_correct': sum(row['completed_correct'] for row in rows),
            'correct_artifacts': sum(row['correct'] for row in rows),
            'timeouts': sum(row.get('timed_out', False) for row in rows),
            'infrastructure_failures': sum(bool(row.get('infrastructure_error')) for row in rows),
            'searches': sum(row.get('verified_dense_searches', 0) for row in rows),
            'measurements': measures,
            'full_inventory_tokens': measures['total_tokens']['sum']
            if measures['total_tokens']['observations'] == expected else None}
    grouped = {}
    for row in records:
        grouped.setdefault((row['task_id'], row['repetition']), {})[row['condition']] = row
    pairs = []
    for key, members in grouped.items():
        if set(members) != set(CONDITIONS):
            continue
        left, right = members['baseline'], members['mcp']
        require(left.get('base_tree_sha256') == right.get('base_tree_sha256')
                and left.get('prompt_sha256') == right.get('prompt_sha256'), 'Paired fixtures/prompts differ')
        pairs.append((key, left, right))
    for label in ('all_pairs', 'normally_completed_pairs', 'both_correct_pairs'):
        eligible = [(key, a, b) for key, a, b in pairs
                    if label == 'all_pairs' or normal(a) and normal(b)
                    and (label != 'both_correct_pairs' or a['completed_correct'] and b['completed_correct'])]
        metrics = {}
        for field in FIELDS:
            measured = [(key, a, b) for key, left, right in eligible
                        if (a := value(left, field)) is not None and (b := value(right, field)) is not None]
            a, b = sum(x for _, x, _ in measured), sum(y for _, _, y in measured)
            metrics[field] = {'pairs': len(measured), 'members': [list(key) for key, _, _ in measured],
                              'baseline_sum': a if measured else None, 'mcp_sum': b if measured else None,
                              'change_pct': 100 * (b / a - 1) if measured and a else None}
        result['comparisons'][label] = {'pairs': len(eligible), 'measurements': metrics}
    return result


def normal(row):
    return bool(row.get('completed') and row.get('agent_exit_code') == 0
                and not row.get('failed') and not row.get('timed_out') and not row.get('infrastructure_error'))


def retain_receipts(row, raw, public):
    file = raw / 'http-receipts.jsonl'
    receipts = [json.loads(line) for line in file.read_text().splitlines()] if file.exists() else []
    successful = [r for r in receipts if r.get('response') is not None and not r.get('error')]
    row['verified_dense_searches'] = sum(
        r['response'].get('retrieval') == 'dense' and r['response'].get('rerank') is False
        and not r.get('snapshot_error')
        and len(r.get('returned_source_checks', [])) == len(r['response']['results'])
        and all(all(check.get(field) is True for field in
                    ('source_matches_snapshot', 'physical_bounds_match', 'source_hash_matches'))
                for check in r.get('returned_source_checks', []))
        for r in successful)
    row['actual_search_settings'] = [r['settings'] for r in receipts]
    row['http_searches'] = len(receipts)
    row['successful_http_searches'] = len(successful)
    # Retain exact saved-source snapshots, outside all candidate checkouts.
    if (raw / 'snapshots').exists():
        with tarfile.open(public / 'source-snapshots.tar.gz', 'w:gz') as archive:
            archive.add(raw / 'snapshots', arcname='snapshots')
    for name in ('agent-measurements.json', 'agent-process.json'):
        if (raw / name).exists():
            mf.write_json(public / name, mf.public_redact(read(raw / name),
                          [(str(ROOT), '<PROJECT>'), (str(Path.home()), '<HOME>')]))


def run(output):
    config = read(output / 'frozen-config.json')
    mf.verify_runtime(config)
    require(not (output / 'run-start.json').exists(), 'No retries or resumed trials')
    mf.write_json(output / 'run-start.json', {'started': capture.stamp(), 'config_sha256': mf.old.digest(output / 'frozen-config.json')})
    manifest = read(output / 'tasks.json')
    require(manifest == read(MANIFEST), 'Task snapshot differs from frozen manifest')
    tasks = {task['id']: task for task in manifest['tasks']}
    mf.run_agent, mf.prompt = capture.run_agent, prompt
    records = []
    clock = ClockIntegrity()
    keepawake = subprocess.Popen(['caffeinate', '-dimsu', '-w', str(__import__('os').getpid())]) if shutil.which('caffeinate') else None
    try:
        for number, trial in enumerate(config['schedule'], 1):
            mf.verify_runtime(config)
            begin = clock.check(f'attempt-{number}-start')
            row = mf.run_trial(tasks[trial['task_id']], trial, number, config, output)
            row['clock_start'] = begin
            try:
                row['clock_end'] = clock.check(f'attempt-{number}-end', since=begin['sample'])
            except ClockIntegrityError as error:
                row['clock_end'] = error.receipt
                row['timing_qualified'] = False
            if trial['condition'] == 'baseline':
                row['service_setup_s'] = 0.0
            row['full_trial_elapsed_s'] = (row['phase_timestamps']['trial_end']['monotonic_s']
                                         - row['phase_timestamps']['fixture_start']['monotonic_s'])
            public = output / 'runs' / row['run_id']
            raw = Path(config['work_root']) / 'raw' / row['run_id']
            retain_receipts(row, raw, public)
            mf.write_json(public / 'result.json', row)
            records.append(row)
            mf.write_json(output / 'results.json', records)
            summary = summarize(records, config['schedule'])
            mf.write_json(output / 'summary.json', summary)
            print(f'Finished {row["run_id"]}: correct={row["completed_correct"]}, '
                  f'agent_s={row.get("agent_elapsed_s")}, searches={row["successful_http_searches"]}', flush=True)
            if row.get('interrupted'):
                break
        mf.verify_runtime(config)
        mf.write_json(output / 'completion.json', {'finished': capture.stamp(), 'recorded_attempts': len(records),
                      'inventory_complete': len(records) == len(config['schedule']),
                      'config_sha256': mf.old.digest(output / 'frozen-config.json')})
    finally:
        if keepawake is not None:
            keepawake.terminate()
            keepawake.wait(timeout=10)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('freeze', 'run'))
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--work-root', type=Path)
    parser.add_argument('--common-config', type=Path)
    parser.add_argument('--probe-result', type=Path)
    args = parser.parse_args()
    if args.action == 'freeze':
        freeze(args.output, args.work_root, args.common_config, args.probe_result)
    else:
        with capture.guarded_sigterm():
            run(args.output)


if __name__ == '__main__':
    main()
