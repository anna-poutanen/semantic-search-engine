#!/usr/bin/env python3
"""Post-output compatibility probe; supplemental, never changes frozen grades.

Manual review found that combining normalization and case folding into one
per-choice step may change existing collision ordering even without aliases.
Compare completed Click candidates to unchanged pinned upstream and reference.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PROGRAM = '''import json,sys
from pathlib import Path
repo=Path(sys.argv[1]).resolve()
sys.path.insert(0,str(repo/'src'))
import click
assert Path(click.__file__).resolve().is_relative_to(repo/'src')
choices=['A_B','a_b','A-B']
ctx=click.Context(click.Command('probe'),token_normalize_func=lambda s:s.replace('_','-'))
choice=click.Choice(choices,case_sensitive=False)
print(json.dumps({'result':choice.convert('a_b',None,ctx),'choices':choices}))
'''


def probe(repo):
    process = subprocess.run([sys.executable, '-B', '-c', PROGRAM, str(repo)],
                             capture_output=True, text=True, timeout=30)
    return {'exit_code': process.returncode, 'output': json.loads(process.stdout) if process.returncode == 0 else None,
            'stderr': process.stderr}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--study', type=Path, required=True)
    args = parser.parse_args()
    config = json.loads((args.study / 'frozen-config.json').read_text())
    tasks = json.loads((args.study / 'tasks.json').read_text())['tasks']
    task = next(t for t in tasks if t['id'] == 'click_choice_aliases')
    upstream = probe(ROOT / task['corpus'])
    if upstream['exit_code'] != 0 or upstream['output']['result'] != 'a_b':
        raise ValueError('Pinned upstream control differs')
    reference = probe((ROOT / task['reference_patch']).parent / 'reference')
    if reference['output'] != upstream['output']:
        raise ValueError('Evaluator reference must preserve upstream collision ordering')
    rows = json.loads((args.study / 'results.json').read_text())
    findings = []
    for row in rows:
        if row['task_id'] != task['id']:
            continue
        repo = Path(config['work_root']) / 'workspaces' / row['run_id']
        result = probe(repo)
        findings.append({'run_id': row['run_id'], 'condition': row['condition'],
                         'frozen_completed_correct': row['completed_correct'], 'observed': result,
                         'preserves_upstream': result['exit_code'] == 0 and result['output'] == upstream['output'],
                         'retained_patch_sha256': row['patch_sha256']})
    output = {'classification': 'Supplemental post-output probe authored after manual review; frozen grades unchanged',
              'public_contract': 'Preserve behavior without aliases',
              'scenario': 'No aliases; choices A_B, a_b, A-B; underscore-to-hyphen token normalizer; case-insensitive conversion of a_b',
              'reason': 'Upstream first deduplicates normalized names then casefolds; fusing stages changes canonical collision priority',
              'upstream': upstream, 'reference': reference, 'results': findings,
              'probe_source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'findings': [row['run_id'] for row in findings if not row['preserves_upstream']]}
    (args.study / 'supplemental-compatibility.json').write_text(json.dumps(output, indent=2) + '\n')
    print(json.dumps({'checked': len(findings), 'regressions': output['findings']}, indent=2))


if __name__ == '__main__':
    main()
