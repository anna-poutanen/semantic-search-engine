#!/usr/bin/env python3
"""Run only unattempted frozen trials after an externally interrupted segment.

Never retry a coding attempt or resume a conversation. Original results are
preserved byte-for-byte in a segment archive before appending unattempted IDs.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import signal
import subprocess
import threading

import benchmark_agent_grep as bench
import qwen_workflow_process as capture
import run_multifile_workflows as mf
from qwen_optimization_clock import ClockIntegrity, ClockIntegrityError


def freeze(study, segment_file, probe):
    bench.require(not segment_file.exists(), 'Continuation needs a fresh segment file')
    config = bench.read(study / 'frozen-config.json')
    mf.verify_runtime(config)
    diagnostic = bench.read(probe)
    bench.require(diagnostic.get('completed_correct') is True
                  and diagnostic['common_runtime_sha256'] == config['common_runtime_sha256'],
                  'Passing fresh transport under the identical common runtime is required')
    rows = bench.read(study / 'results.json')
    bench.require(rows and len(rows) < len(config['schedule']) and rows[-1].get('interrupted'),
                  'Only an externally interrupted incomplete segment can continue')
    for row, trial in zip(rows, config['schedule']):
        bench.require(all(row[key] == trial[key] for key in ('task_id', 'condition', 'repetition')),
                      'Observed prefix differs from original frozen schedule')
    for number, trial in enumerate(config['schedule'][len(rows):], len(rows) + 1):
        name = f"{number:02d}-r{trial['repetition']}-{trial['task_id']}-{trial['condition']}"
        bench.require(not (study / 'runs' / name).exists()
                      and not (Path(config['work_root']) / 'raw' / name).exists(),
                      'An already-attempted identity cannot run again: ' + name)
    archive = study / (segment_file.stem + '-prior-segment')
    archive.mkdir()
    prior = {}
    for name in ('results.json', 'summary.json', 'completion.json', 'evidence-audit.json',
                 'qualified-summary.json', 'qualified-measurements.json', 'external-interruption.json'):
        if (study / name).exists():
            shutil.copy2(study / name, archive / name)
            prior[name] = mf.old.digest(study / name)
    segment = {'version': 1, 'frozen_at': capture.stamp(), 'classification': 'Unattempted schedule continuation; no coding attempt retry/resume',
               'frozen_config_sha256': mf.old.digest(study / 'frozen-config.json'),
               'prefix_attempts': len(rows), 'prefix_results_sha256': mf.old.digest(study / 'results.json'),
               'prior_segment_directory': archive.name, 'prior_artifact_sha256': prior,
               'remaining_schedule': config['schedule'][len(rows):],
               'fresh_transport_sha256': mf.old.digest(probe), 'fresh_transport': str(probe),
               'continuation_driver_sha256': mf.old.digest(Path(__file__)),
               'original_conditions_changed': False, 'original_grades_changed': False,
               'interrupted_attempts_retried': False,
               'clock_monitor': 'Check wall/performance drift every second; signal owned runner on >2s drift'}
    mf.write_json(segment_file, segment)
    print(f'Frozen {len(segment["remaining_schedule"])} unattempted IDs; retained original {len(rows)} attempts', flush=True)


def run(study, segment_file):
    segment = bench.read(segment_file)
    config = bench.read(study / 'frozen-config.json')
    mf.verify_runtime(config)
    bench.require(mf.old.digest(Path(__file__)) == segment['continuation_driver_sha256']
                  and mf.old.digest(study / 'frozen-config.json') == segment['frozen_config_sha256']
                  and mf.old.digest(study / 'results.json') == segment['prefix_results_sha256'],
                  'Continuation inputs changed after freeze')
    start_file = segment_file.with_name(segment_file.stem + '-start.json')
    bench.require(not start_file.exists(), 'A continuation segment cannot be restarted')
    records = bench.read(study / 'results.json')
    prefix = bench.read(study / segment['prior_segment_directory'] / 'results.json')
    bench.require(records == prefix, 'Previously retained prefix was modified')
    tasks = {task['id']: task for task in bench.read(study / 'tasks.json')['tasks']}
    mf.run_agent, mf.prompt = capture.run_agent, bench.prompt
    clock, stopped = ClockIntegrity(), threading.Event()
    mf.write_json(start_file, {'started': capture.stamp(), 'segment_sha256': mf.old.digest(segment_file)})

    def monitor():
        while not stopped.wait(1):
            try:
                clock.check('continuation-background-monitor')
            except ClockIntegrityError as error:
                mf.write_json(segment_file.with_name(segment_file.stem + '-clock-interruption.json'), error.receipt)
                os.kill(os.getpid(), signal.SIGTERM)
                break

    worker = threading.Thread(target=monitor, daemon=True)
    worker.start()
    keepawake = subprocess.Popen(['caffeinate', '-dimsu', '-w', str(os.getpid())]) if shutil.which('caffeinate') else None
    try:
        for number, trial in enumerate(segment['remaining_schedule'], segment['prefix_attempts'] + 1):
            mf.verify_runtime(config)
            begin = clock.check(f'continued-attempt-{number}-start')
            row = mf.run_trial(tasks[trial['task_id']], trial, number, config, study)
            row['clock_start'] = begin
            row['capture_segment'] = segment_file.name
            try:
                row['clock_end'] = clock.check(f'continued-attempt-{number}-end', since=begin['sample'])
            except ClockIntegrityError as error:
                row['clock_end'], row['timing_qualified'] = error.receipt, False
            if trial['condition'] == 'baseline':
                row['service_setup_s'] = 0.0
            row['full_trial_elapsed_s'] = (row['phase_timestamps']['trial_end']['monotonic_s']
                                         - row['phase_timestamps']['fixture_start']['monotonic_s'])
            public = study / 'runs' / row['run_id']
            raw = Path(config['work_root']) / 'raw' / row['run_id']
            bench.retain_receipts(row, raw, public)
            mf.write_json(public / 'result.json', row)
            records.append(row)
            bench.require(records[:len(prefix)] == prefix, 'Continuation changed previously observed outcomes')
            mf.write_json(study / 'results.json', records)
            mf.write_json(study / 'summary.json', bench.summarize(records, config['schedule']))
            print(f'Finished {row["run_id"]}: correct={row["completed_correct"]}, '
                  f'agent_s={row.get("agent_elapsed_s")}, searches={row["successful_http_searches"]}', flush=True)
            if row.get('interrupted') or not row['clock_end']['consistent']:
                break
        mf.verify_runtime(config)
        completion = {'finished': capture.stamp(), 'recorded_attempts': len(records),
                      'inventory_complete': len(records) == len(config['schedule']),
                      'config_sha256': mf.old.digest(study / 'frozen-config.json'),
                      'continuation_segment_sha256': mf.old.digest(segment_file),
                      'prior_segment_preserved': True, 'coding_attempts_retried': 0}
        mf.write_json(study / 'completion.json', completion)
        mf.write_json(segment_file.with_name(segment_file.stem + '-completion.json'), completion)
    finally:
        stopped.set()
        worker.join(5)
        if keepawake is not None:
            keepawake.terminate()
            keepawake.wait(timeout=10)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('freeze', 'run'))
    parser.add_argument('--study', type=Path, required=True)
    parser.add_argument('--segment-file', type=Path, required=True)
    parser.add_argument('--probe-result', type=Path)
    args = parser.parse_args()
    if args.action == 'freeze':
        freeze(args.study, args.segment_file, args.probe_result)
    else:
        with capture.guarded_sigterm():
            run(args.study, args.segment_file)


if __name__ == '__main__':
    main()
