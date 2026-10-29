"""Withheld public-behavior checks for the fresh search-versus-grep study.

Run outside the candidate checkout. Expected behavior is stated in each prompt;
these checks never import candidate tests or use an agent's own success claims.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import pickle
import sys
import unittest


class RequestsTimeout(unittest.TestCase):
    def session(self, **kwargs):
        import requests
        from requests.adapters import BaseAdapter

        class Adapter(BaseAdapter):
            def __init__(self):
                self.calls = []

            def send(self, request, **options):
                self.calls.append((request, options.copy()))
                response = requests.Response()
                response.request, response.url = request, request.url
                response.status_code = 302 if request.url.endswith('/redirect') else 200
                response._content = b'ok'
                if response.status_code == 302:
                    response.headers['Location'] = '/done'
                return response

            def close(self):
                pass

        session = requests.Session(**kwargs)
        session.trust_env = False
        adapter = Adapter()
        session.mount('http://', adapter)
        return session, adapter

    def test_default_remains_unlimited(self):
        session, adapter = self.session()
        self.assertIsNone(session.timeout)
        session.get('http://example.test/')
        self.assertIsNone(adapter.calls[-1][1]['timeout'])

    def test_constructor_default(self):
        session, adapter = self.session(timeout=3.25)
        session.get('http://example.test/')
        self.assertEqual(adapter.calls[-1][1]['timeout'], 3.25)

    def test_mutable_default(self):
        session, adapter = self.session()
        session.timeout = (1.0, 4.0)
        session.post('http://example.test/', data=b'hello')
        self.assertEqual(adapter.calls[-1][1]['timeout'], (1.0, 4.0))
        session.timeout = None
        session.get('http://example.test/')
        self.assertIsNone(adapter.calls[-1][1]['timeout'])

    def test_explicit_none_disables(self):
        session, adapter = self.session(timeout=7)
        session.get('http://example.test/', timeout=None)
        self.assertIsNone(adapter.calls[-1][1]['timeout'])
        self.assertEqual(session.timeout, 7)

    def test_explicit_override_does_not_mutate(self):
        session, adapter = self.session(timeout=7)
        session.request('GET', 'http://example.test/', timeout=(2, 5))
        session.get('http://example.test/')
        self.assertEqual([options['timeout'] for _, options in adapter.calls], [(2, 5), 7])

    def test_method_helpers(self):
        session, adapter = self.session(timeout=9)
        for method in ('get', 'options', 'head', 'post', 'put', 'patch', 'delete'):
            getattr(session, method)('http://example.test/')
        self.assertEqual([options['timeout'] for _, options in adapter.calls], [9] * 7)

    def test_prepared_send(self):
        import requests
        session, adapter = self.session(timeout=8)
        for options in ({}, {'timeout': None}, {'timeout': (1, 2)}):
            prepared = session.prepare_request(requests.Request('GET', 'http://example.test/'))
            session.send(prepared, **options)
        self.assertEqual([options['timeout'] for _, options in adapter.calls], [8, None, (1, 2)])

    def test_redirect_chain_inherits_effective_timeout(self):
        for options, expected in (({}, 6), ({'timeout': None}, None), ({'timeout': 2}, 2)):
            with self.subTest(options=options):
                session, adapter = self.session(timeout=6)
                response = session.get('http://example.test/redirect', **options)
                self.assertEqual(len(response.history), 1)
                self.assertEqual([settings['timeout'] for _, settings in adapter.calls], [expected] * 2)

    def test_pickling(self):
        import requests
        for value in (None, 3.5, (1, 4)):
            session = requests.Session(timeout=value)
            restored = pickle.loads(pickle.dumps(session))
            self.assertEqual(restored.timeout, value)
            self.assertEqual(restored.__getstate__()['timeout'], value)

    def test_module_helpers_unchanged(self):
        import requests
        from unittest.mock import patch
        session, adapter = self.session()
        with patch('requests.api.sessions.Session', return_value=session):
            requests.get('http://example.test/', timeout=2.5)
        self.assertEqual(adapter.calls[-1][1]['timeout'], 2.5)


class ClickAliases(unittest.TestCase):
    def choice(self, choices=('production', 'development'), **kwargs):
        import click
        return click.Choice(choices, **kwargs)

    def test_alias_returns_canonical_object(self):
        canonical = ''.join(['produc', 'tion'])
        choice = self.choice((canonical, 'development'), aliases={'prod': canonical})
        self.assertIs(choice.convert('prod', None, None), canonical)

    def test_original_names_still_work(self):
        choice = self.choice(aliases={'prod': 'production'})
        self.assertEqual(choice.convert('development', None, None), 'development')

    def test_casefold_applies_to_aliases(self):
        choice = self.choice(aliases={'PrOd': 'production'}, case_sensitive=False)
        self.assertEqual(choice.convert('PROD', None, None), 'production')

    def test_context_normalization(self):
        import click
        ctx = click.Context(click.Command('demo'), token_normalize_func=lambda value: value.replace('_', '-'))
        choice = self.choice(aliases={'live-mode': 'production'})
        self.assertEqual(choice.convert('live_mode', None, ctx), 'production')

    def test_defensive_copy(self):
        aliases = {'prod': 'production'}
        choice = self.choice(aliases=aliases)
        aliases['prod'] = 'development'
        self.assertEqual(choice.convert('prod', None, None), 'production')

    def test_invalid_targets_and_types(self):
        for aliases in ({'p': 'missing'}, {1: 'production'}, {'p': 1}, [('p', 'production')]):
            with self.subTest(aliases=aliases), self.assertRaises(ValueError):
                self.choice(aliases=aliases)

    def test_alias_collision_with_canonical_rejected_at_conversion(self):
        import click
        choice = self.choice(aliases={'PRODUCTION': 'development'}, case_sensitive=False)
        with self.assertRaises(click.BadParameter):
            choice.convert('production', None, None)

    def test_alias_collision_with_other_alias(self):
        import click
        choice = self.choice(aliases={'prod': 'production', 'PROD': 'development'}, case_sensitive=False)
        with self.assertRaises(click.BadParameter):
            choice.convert('prod', None, None)

    def test_equivalent_aliases_same_target_are_allowed(self):
        choice = self.choice(aliases={'prod': 'production', 'PROD': 'production'}, case_sensitive=False)
        self.assertEqual(choice.convert('pRoD', None, None), 'production')

    def test_context_can_introduce_collision(self):
        import click
        ctx = click.Context(click.Command('demo'), token_normalize_func=lambda value: value.replace('_', '-'))
        choice = self.choice(aliases={'x-y': 'production', 'x_y': 'development'})
        with self.assertRaises(click.BadParameter):
            choice.convert('x-y', None, ctx)

    def test_help_errors_and_completion_stay_canonical(self):
        import click
        from click.testing import CliRunner
        choice = self.choice(aliases={'prod': 'production'})
        @click.command()
        @click.option('--mode', type=choice)
        def command(mode):
            click.echo(mode)
        runner = CliRunner()
        self.assertEqual(runner.invoke(command, ['--mode', 'prod']).output, 'production\n')
        self.assertIn('[production|development]', runner.invoke(command, ['--help']).output)
        invalid = runner.invoke(command, ['--mode', 'wrong'])
        self.assertNotEqual(invalid.exit_code, 0)
        self.assertIn("'production', 'development'", invalid.output)
        ctx = click.Context(command)
        self.assertEqual([item.value for item in choice.shell_complete(ctx, command.params[0], '')],
                         ['production', 'development'])


class BackgroundContinuation(unittest.TestCase):
    def run_case(self, body):
        import anyio
        for backend in ('asyncio', 'trio'):
            with self.subTest(backend=backend):
                anyio.run(body, backend=backend)

    def test_default_stops_on_first_error(self):
        from starlette.background import BackgroundTasks
        async def body():
            calls = []
            async def first():
                calls.append(1)
                raise ValueError('first')
            async def second():
                calls.append(2)
            tasks = BackgroundTasks()
            tasks.add_task(first)
            tasks.add_task(second)
            with self.assertRaises(ValueError):
                await tasks()
            self.assertEqual(calls, [1])
        self.run_case(body)

    def test_continues_in_order_and_raises_first_identical_exception(self):
        from starlette.background import BackgroundTasks
        async def body():
            calls = []
            original = ValueError('first')
            async def first():
                calls.append(1)
                raise original
            def second():
                calls.append(2)
                raise RuntimeError('second')
            async def third():
                calls.append(3)
            tasks = BackgroundTasks(continue_on_error=True)
            tasks.add_task(first)
            tasks.add_task(second)
            tasks.add_task(third)
            try:
                await tasks()
            except ValueError as error:
                self.assertIs(error, original)
                import traceback
                self.assertIn('first', [frame.name for frame in traceback.extract_tb(error.__traceback__)])
            else:
                self.fail('first exception must be raised')
            self.assertEqual(calls, [1, 2, 3])
        self.run_case(body)

    def test_success_with_initial_sequence_and_arguments(self):
        from starlette.background import BackgroundTask, BackgroundTasks
        async def body():
            calls = []
            def sync(value, *, extra):
                calls.append(value + extra)
            tasks = BackgroundTasks([BackgroundTask(sync, 2, extra=3)], continue_on_error=True)
            tasks.add_task(sync, 4, extra=5)
            self.assertIsNone(await tasks())
            self.assertEqual(calls, [5, 9])
        self.run_case(body)

    def test_base_exception_is_never_suppressed(self):
        from starlette.background import BackgroundTasks
        class Stop(BaseException):
            pass
        async def body():
            calls = []
            stop = Stop()
            async def first():
                calls.append(1)
                raise stop
            async def second():
                calls.append(2)
            tasks = BackgroundTasks(continue_on_error=True)
            tasks.add_task(first)
            tasks.add_task(second)
            with self.assertRaises(Stop) as raised:
                await tasks()
            self.assertIs(raised.exception, stop)
            self.assertEqual(calls, [1])
        self.run_case(body)

    def test_cancellation_after_ordinary_error_wins(self):
        from starlette.background import BackgroundTasks
        class Stop(BaseException):
            pass
        async def body():
            calls = []
            async def ordinary():
                calls.append(1)
                raise ValueError('first')
            async def stop():
                calls.append(2)
                raise Stop()
            async def last():
                calls.append(3)
            tasks = BackgroundTasks(continue_on_error=True)
            for callback in (ordinary, stop, last):
                tasks.add_task(callback)
            with self.assertRaises(Stop):
                await tasks()
            self.assertEqual(calls, [1, 2])
        self.run_case(body)

    def test_requires_boolean(self):
        from starlette.background import BackgroundTasks
        for value in (0, 1, 'yes', None, []):
            with self.subTest(value=value), self.assertRaises(ValueError):
                BackgroundTasks(continue_on_error=value)

    def test_response_integration(self):
        from starlette.background import BackgroundTasks
        from starlette.responses import Response
        from starlette.testclient import TestClient
        for backend in ('asyncio', 'trio'):
            calls = []
            def first():
                calls.append('first')
                raise ValueError('cleanup')
            async def second():
                calls.append('second')
            async def app(scope, receive, send):
                tasks = BackgroundTasks(continue_on_error=True)
                tasks.add_task(first)
                tasks.add_task(second)
                await Response('ok', background=tasks)(scope, receive, send)
            with self.subTest(backend=backend):
                client = TestClient(app, backend=backend)
                with self.assertRaises(ValueError):
                    client.get('/')
                self.assertEqual(calls, ['first', 'second'])
                client.close()


class QuotedHeaders(unittest.TestCase):
    def split(self, value):
        import httpx
        return httpx.Headers({'X-Test': value}).get_list('x-test', split_commas=True)

    def test_quoted_commas(self):
        self.assertEqual(self.split('token, "quoted, pair", last'), ['token', '"quoted, pair"', 'last'])

    def test_parameter_quotes(self):
        self.assertEqual(self.split('text/plain; title="a,b", application/json'),
                         ['text/plain; title="a,b"', 'application/json'])

    def test_escaped_quote(self):
        value = r'"a\",b", c'
        self.assertEqual(self.split(value), [r'"a\",b"', 'c'])

    def test_escaped_backslash_then_closing_quote(self):
        value = r'"a\\", b'
        self.assertEqual(self.split(value), [r'"a\\"', 'b'])

    def test_unclosed_quote_keeps_remaining_text(self):
        self.assertEqual(self.split('a, "b,c'), ['a', '"b,c'])

    def test_repeated_headers_reset_quote_state(self):
        import httpx
        headers = httpx.Headers([('X-Test', '"open,a'), ('x-test', 'b, c')])
        self.assertEqual(headers.get_list('X-TEST', split_commas=True), ['"open,a', 'b', 'c'])

    def test_whitespace_and_empty_members(self):
        self.assertEqual(self.split(' a , , " b,c " , '), ['a', '', '" b,c "', ''])
        self.assertEqual(self.split(''), [''])

    def test_backslash_outside_quotes_does_not_escape_comma(self):
        self.assertEqual(self.split(r'a\,b'), ['a\\', 'b'])

    def test_single_quotes_are_ordinary_characters(self):
        self.assertEqual(self.split("'a,b',c"), ["'a", "b'", 'c'])

    def test_default_and_raw_are_unchanged(self):
        import httpx
        headers = httpx.Headers([('X-Test', 'a,"b,c"'), ('x-test', 'd')])
        before = list(headers.raw)
        self.assertEqual(headers.get_list('X-Test'), ['a,"b,c"', 'd'])
        headers.get_list('X-Test', split_commas=True)
        self.assertEqual(headers.raw, before)
        self.assertEqual(headers['x-test'], 'a,"b,c", d')
        self.assertEqual(headers.get_list('missing', split_commas=True), [])


SUITES = {'requests_default_timeout': ('requests', 'src', RequestsTimeout),
          'click_choice_aliases': ('click', 'src', ClickAliases),
          'starlette_background_continuation': ('starlette', '.', BackgroundContinuation),
          'httpx_quoted_headers': ('httpx', '.', QuotedHeaders)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True, type=Path)
    parser.add_argument('--task', choices=tuple(SUITES), required=True)
    args = parser.parse_args()
    package, source, suite = SUITES[args.task]
    root = args.repo.resolve(strict=True)
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(root / source))
    module = __import__(package)
    if not Path(module.__file__).resolve().is_relative_to(root / source):
        raise RuntimeError('Checker imported a package outside the candidate checkout')
    result = unittest.TextTestRunner(stream=sys.stderr, verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(suite))
    print(json.dumps({'tests_run': result.testsRun, 'failures': len(result.failures),
                      'errors': len(result.errors), 'skipped': len(result.skipped),
                      'passed': result.wasSuccessful()}, sort_keys=True))
    raise SystemExit(0 if result.wasSuccessful() else 1)


if __name__ == '__main__':
    main()
