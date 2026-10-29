#!/usr/bin/env python3
"""Prepare new evaluator controls before any search-versus-grep coding trials."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

import run_multifile_workflows as mf

ROOT = mf.ROOT
CHECKER = 'benchmarks/agent_grep_checks.py'

SPECS = [
    ('requests_default_timeout', 'requests', 'src/requests',
     'Add a session-wide default timeout to Requests. Session(timeout=None) must expose a mutable '
     'Session.timeout attribute, defaulting to the current unlimited behavior. A request that omits '
     'timeout inherits the current session value; an explicit timeout=None disables that default '
     'for the request. Other explicit timeout values override the default without mutating it. '
     'Apply this consistently to Session.request, all Session HTTP-method helpers, and direct '
     'Session.send of a PreparedRequest. Every hop in a redirect chain must receive the effective '
     'timeout selected for the initial request. Preserve timeout across pickling. Keep existing '
     'module-level request helpers and ordinary timeout/adapter behavior compatible. Add regression tests.',
     ['tests/test_hooks.py', 'tests/test_structures.py', 'tests/test_utils.py',
      'tests/test_requests.py::TestRequests::test_response_is_iterable',
      'tests/test_requests.py::TestRequests::test_response_decode_unicode',
      'tests/test_requests.py::TestRequests::test_response_chunk_size_type',
      'tests/test_requests.py::TestRequests::test_HTTP_302_ALLOW_REDIRECT_GET',
      'tests/test_requests.py::TestRequests::test_HTTP_307_ALLOW_REDIRECT_POST',
      'tests/test_requests.py::TestRequests::test_response_context_manager']),
    ('click_choice_aliases', 'click', 'src/click',
     'Add optional aliases to click.Choice through a new aliases=None constructor parameter. '
     'aliases is a mapping from string alias names to exact string members of the original choices; '
     'copy the mapping so caller mutation cannot change the type. Reject non-mappings, non-string '
     'keys or values, and targets outside the original choices with ValueError at construction. '
     'Accept aliases during conversion and return the original canonical choice object. Apply '
     'Context.token_normalize_func followed by case folding when case_sensitive=False to both '
     'user input and aliases, just as for canonical choices. If normalized aliases collide with '
     'another alias or canonical name mapping to a different canonical choice, raise BadParameter '
     'during conversion rather than silently choosing one; collisions mapping to the same target '
     'are allowed. Preserve behavior without aliases. Help, invalid-input choice lists, and shell '
     'completion must continue to show canonical choices only. Add regression tests.',
     ['tests/test_types.py', 'tests/test_normalization.py', 'tests/test_shell_completion.py',
      'tests/test_info_dict.py']),
    ('starlette_background_continuation', 'starlette', 'starlette',
     'Add an opt-in continue_on_error keyword-only boolean to BackgroundTasks, defaulting to False. '
     'Reject non-booleans with ValueError at construction. The default must retain stop-on-first-error '
     'behavior. With continue_on_error=True, execute every registered task sequentially in registration '
     'order despite ordinary Exception failures, then re-raise the first exception object with its '
     'original callback traceback retained. Preserve synchronous threadpool dispatch, async callbacks, '
     'initial task sequences, add_task positional/keyword arguments, and response integration. '
     'BaseException failures, including cancellation, must propagate immediately and stop later tasks, '
     'even if an earlier ordinary exception was recorded. Successful and empty task lists still return '
     'None. Cover both asyncio and Trio and add regression tests.',
     ['tests/test_background.py', 'tests/test_responses.py', 'tests/test_testclient.py']),
    ('httpx_quoted_headers', 'httpx', 'httpx',
     'Fix HTTPX Headers.get_list(key, split_commas=True) so commas inside double-quoted strings '
     'do not split a header value, including quoted parameter values such as text/plain; title="a,b". '
     'Inside double quotes a backslash escapes the following character, so escaped quotes do not '
     'end the quoted section and an escaped backslash does not escape the quote after it. Outside '
     'double quotes a backslash has no escaping role; single quotes are ordinary characters. '
     'Preserve original quote and backslash spelling and whitespace inside each member while stripping '
     'surrounding member whitespace. Preserve empty members, including leading/trailing commas and '
     'an empty header. An unmatched opening double quote keeps the rest of that physical header '
     'value as one member; reset parser state between repeated header occurrences. Preserve repeated '
     'header order, case-insensitive lookup, the default split_commas=False behavior and raw stored '
     'headers without mutation. Add regression tests.',
     ['tests/models/test_headers.py', 'tests/client/test_headers.py']),
]


def replace_once(file, old, new):
    source = file.read_text()
    if source.count(old) != 1:
        raise ValueError('Reference replacement is not unique: ' + str(file))
    file.write_text(source.replace(old, new))


def apply_reference(task_id, workspace):
    if task_id == 'requests_default_timeout':
        file = workspace / 'src/requests/sessions.py'
        replace_once(file, 'class Session(SessionRedirectMixin):',
                     '_DEFAULT_TIMEOUT = object()\n\n\nclass Session(SessionRedirectMixin):')
        replace_once(file, '    def __init__(self) -> None:',
                     '    def __init__(self, timeout: _t.TimeoutType = None) -> None:\n        self.timeout = timeout')
        replace_once(file, '        "max_redirects",\n', '        "max_redirects",\n        "timeout",\n')
        source = file.read_text()
        start = source.index('    def request(\n', source.index('class Session('))
        source = source[:start] + source[start:].replace(
            '        timeout: _t.TimeoutType = None,',
            '        timeout: _t.TimeoutType = _DEFAULT_TIMEOUT,', 1)
        source = source.replace('            "timeout": timeout,\n',
                                '            "timeout": self.timeout if timeout is _DEFAULT_TIMEOUT else timeout,\n')
        file.write_text(source)
        replace_once(file, '        kwargs.setdefault("stream", self.stream)',
                     '        kwargs.setdefault("timeout", self.timeout)\n        kwargs.setdefault("stream", self.stream)')
    elif task_id == 'click_choice_aliases':
        file = workspace / 'src/click/types.py'
        replace_once(file, '    def __init__(self, choices: t.Sequence[str], case_sensitive: bool = True) -> None:\n'
                     '        self.choices = choices\n        self.case_sensitive = case_sensitive\n',
                     '''    def __init__(self, choices: t.Sequence[str], case_sensitive: bool = True, aliases=None) -> None:
        from collections.abc import Mapping
        self.choices = choices
        self.case_sensitive = case_sensitive
        if aliases is not None and not isinstance(aliases, Mapping):
            raise ValueError("aliases must be a mapping")
        self.aliases = dict(aliases) if aliases is not None else {}
        if any(not isinstance(k, str) or not isinstance(v, str) or v not in self.choices
               for k, v in self.aliases.items()):
            raise ValueError("aliases must map strings to original choices")
''')
        replace_once(file, '        if normed_value in normed_choices:\n',
                     '''        for alias, target in self.aliases.items():
            if ctx is not None and ctx.token_normalize_func is not None:
                alias = ctx.token_normalize_func(alias)
            if not self.case_sensitive:
                alias = alias.casefold()
            canonical = next(choice for choice in self.choices if choice == target)
            if alias in normed_choices and normed_choices[alias] != canonical:
                self.fail("Ambiguous alias", param, ctx)
            normed_choices[alias] = canonical

        if normed_value in normed_choices:
''')
    elif task_id == 'starlette_background_continuation':
        file = workspace / 'starlette/background.py'
        replace_once(file, '    def __init__(self, tasks: typing.Sequence[BackgroundTask] | None = None):\n',
                     '''    def __init__(self, tasks: typing.Sequence[BackgroundTask] | None = None, *, continue_on_error: bool = False):
        if type(continue_on_error) is not bool:
            raise ValueError("continue_on_error must be a boolean")
        self.continue_on_error = continue_on_error
''')
        replace_once(file, '        for task in self.tasks:\n            await task()\n',
                     '''        first_error = None
        first_traceback = None
        for task in self.tasks:
            try:
                await task()
            except Exception as error:
                if not self.continue_on_error:
                    raise
                if first_error is None:
                    first_error, first_traceback = error, error.__traceback__
        if first_error is not None:
            raise first_error.with_traceback(first_traceback)
''')
    elif task_id == 'httpx_quoted_headers':
        file = workspace / 'httpx/_models.py'
        replace_once(file, '            split_values.extend([item.strip() for item in value.split(",")])\n',
                     '''            quoted = escaped = False
            start = 0
            for index, char in enumerate(value):
                if quoted and escaped:
                    escaped = False
                elif quoted and char == "\\\\":
                    escaped = True
                elif char == '"':
                    quoted = not quoted
                elif char == "," and not quoted:
                    split_values.append(value[start:index].strip())
                    start = index + 1
            split_values.append(value[start:].strip())
''')
    else:
        raise ValueError(task_id)


def check(task, workspace):
    result = subprocess.run([sys.executable, str(ROOT / CHECKER), '--repo', str(workspace),
                             '--task', task['id']], capture_output=True, text=True,
                            env=mf.environment(workspace, task['source_root']), timeout=90)
    return {'exit_code': result.returncode, 'detail': json.loads(result.stdout.strip().splitlines()[-1]),
            'stdout': result.stdout, 'stderr': result.stderr}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--controls', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    args = parser.parse_args()
    if args.controls.exists() or args.manifest.exists():
        raise ValueError('Controls and manifest must use fresh paths')
    args.controls.mkdir(parents=True)
    previous = json.loads((ROOT / 'benchmarks/coding_workflows_multifile.json').read_text())
    entries = {item['corpus_id']: item for item in previous['tasks']}
    entries['httpx'] = json.loads((ROOT / 'benchmarks/httpx_reranker_manifest.json').read_text())['datasets'][0]
    tasks = []
    for task_id, corpus, source_root, prompt, tests in SPECS:
        entry = entries[corpus]
        task = {key: entry[key] for key in ('corpus', 'revision', 'repo_url')}
        task.update(id=task_id, corpus_id=corpus, source_root=source_root, prompt=prompt,
                    preparation_patch=None, preparation_patch_sha256=None,
                    verification={'script': CHECKER, 'sha256': mf.old.digest(ROOT / CHECKER),
                                  'args': ['--repo', '{workspace}', '--task', task_id], 'timeout_s': 90})
        directory = args.controls / task_id
        directory.mkdir()
        workspace = directory / 'reference'
        _, files = mf.prepare(task, workspace)
        baseline = check(task, workspace)
        mf.write_json(directory / 'baseline.json', baseline)
        if baseline['detail']['passed']:
            raise ValueError('New behavior must be absent from upstream: ' + task_id)
        apply_reference(task_id, workspace)
        reference = check(task, workspace)
        mf.write_json(directory / 'reference.json', reference)
        if not reference['detail']['passed']:
            raise ValueError('Reference failed; inspect retained evidence: ' + task_id)
        task['seed_validation'] = {'base': baseline['detail'], 'reference': reference['detail']}
        patch = directory / 'reference.patch'
        patch.write_bytes(mf.old.git(workspace, 'diff', '--binary').stdout)
        task['reference_patch'] = str(patch.relative_to(ROOT))
        task['reference_patch_sha256'] = mf.old.digest(patch)
        specification = {'args': ['-m', 'pytest', '-q', '-o', 'addopts=', '--tb=short', *tests], 'timeout_s': 180}
        # Expected counts come from unmodified upstream, not the reference implementation.
        baseline_workspace = directory / 'upstream'
        mf.prepare(task, baseline_workspace)
        result = subprocess.run([sys.executable, *specification['args']], cwd=baseline_workspace,
                                env=mf.environment(baseline_workspace, source_root),
                                capture_output=True, text=True, timeout=180)
        counts = mf.pytest_counts(result.stdout)
        mf.write_json(directory / 'upstream-compatibility.json',
                      {'exit_code': result.returncode, 'counts': counts, 'stdout': result.stdout, 'stderr': result.stderr})
        if result.returncode or counts['passed'] == 0:
            raise ValueError('Original upstream gate failed: ' + task_id)
        specification['expected_summary'] = counts
        task['compatibility_commands'] = [specification]
        compatibility = mf.compatibility(task, workspace, files, directory)
        mf.write_json(directory / 'reference-compatibility.json', compatibility)
        if not compatibility['passed']:
            raise ValueError('Reference breaks frozen compatibility: ' + task_id)
        task['control_artifact_sha256'] = {
            str((directory / name).relative_to(ROOT)): mf.old.digest(directory / name)
            for name in ('baseline.json', 'reference.json', 'upstream-compatibility.json',
                         'reference-compatibility.json', 'reference.patch')}
        tasks.append(task)
        print(f'Validated {task_id}: {reference["detail"]["tests_run"]} behavioral methods; '
              f'{counts["passed"]} upstream passes', flush=True)
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    mf.write_json(args.manifest, {'version': 1, 'classification': 'Four new source-authored maintenance tasks; observed repositories',
                                 'selection': 'Selected from public API behavior before any retrieval query or coding-agent output',
                                 'tasks': tasks})


if __name__ == '__main__':
    main()
