"""Forensic prefix/transport/cleanup audit; no semantic quality or winner evaluation."""
import gzip
import hashlib
import importlib.abc
import json
import os
from pathlib import Path
import socket
import sys
import types
from datetime import datetime, timezone

ROOT = Path.cwd()
BASE = ROOT / 'results/context-holdout-transport-repair-2026-10-14/independent-failure-prefix'
STUDY = ROOT / 'results/context-holdout-werkzeug-2026-10-14'
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(('semantic_search', 'run_context_holdout', 'run_django_index_profile', 'django_index_profile_core', 'mlx', 'transformers', 'torch')):
            raise AssertionError('Producer/model import forbidden: ' + fullname)
sys.meta_path.insert(0, Guard())
sys.path.insert(0, str(ROOT / 'scripts'))
raw = gzip.decompress((BASE / 'preserved/audit_context_holdout.py.gz').read_bytes())
old = types.ModuleType('frozen_pretransport_independent_auditor')
old.__file__ = str(ROOT / 'scripts/audit_context_holdout.py')
exec(compile(raw, old.__file__, 'exec'), old.__dict__)
from tokenizers import Tokenizer
common = old.common
require, sha, read = common.require, common.sha, common.read
plan, parent, worker = [read(STUDY / n) for n in ('frozen-plan.json', 'parent-result.json', 'worker-result.json')]
work = common.local_path(plan['work'])
rows = common.rows(STUDY / 'searches.jsonl.gz')
attempts = common.rows(STUDY / 'actual-http-attempts.jsonl.gz')
events = common.rows(STUDY / 'native-forwards.jsonl.gz')
require(parent['complete'] is True and parent['checks_passed'] is False and parent['exit_code'] == 1
        and worker['complete'] is False and worker['checks_passed'] is False, 'Failed attempt classification differs')
require(worker['failure']['error_type'] == 'RuntimeError' and 'HTTP 400' in worker['failure']['error']
        and 'request_id must contain 1–64' in worker['failure']['error'], 'Failure is not the exact transport ID contract')
require(len(rows) == len(attempts) == 8 and all(r['status'] == 'completed' for r in rows[:7])
        and rows[-1]['status'] == attempts[-1]['status'] == 'failed', 'Failed prefix inventory differs')
require(old.expected_schedule([q['id'] for q in plan['manifest']['queries']]) == plan['schedule'], 'Original pretransport schedule differs')
for i, (row, attempt) in enumerate(zip(rows, attempts, strict=True)):
    require(all(common.typed_equal(row[k], v) for k, v in plan['schedule'][i].items()), 'Original prefix schedule differs')
    require(attempt['request_id'] == row['request_id'] and attempt['status'] == row['status'], 'Backend attempt identity differs')
    if i < 7:
        require(len(row['request_id']) <= 64 and len(row['literal_pairs']) == 24, 'Successful actual request pair count differs')
        for counter in old.COUNTERS:
            require(type(row['native_before'][counter]) is type(row['native_after'][counter]) is int
                    and row['native_before'][counter] == 1 + i * 24
                    and row['native_after'][counter] == 1 + (i + 1) * 24, 'Actual per-HTTP native prefix counts differ')
        require(common.typed_equal(row['embedding_delta'], {'document_inputs': 0, 'query_inputs': 1, 'attempted_forwards': 1, 'completed_forwards': 1}),
                'Successful actual request reused cached query or inferred documents')
    else:
        require(len(row['request_id']) > 64 and attempt['embedding_before'] == attempt['embedding_after']
                == {'document_inputs': 1025, 'query_inputs': 7, 'attempted_forwards': 264, 'completed_forwards': 264},
                'Transport rejected request performed unexpected embedding inference')
        require('literal_pairs' not in attempt and 'full_pool' not in attempt and 'response' not in row,
                'Transport rejected request produced retrieval/scoring output')
require(len(events) == 338 and [e['event'] for e in events] == [v for _ in range(169) for v in ('started', 'completed')],
        'Partial actual native ledger has missing/unknown/interleaved events')
tokenizer = Tokenizer.from_file(str(common.local_path(plan['reranker_checkpoint']) / 'tokenizer.json'))
pairs = [p for row in rows[:7] for p in row['literal_pairs']]
require(len(pairs) == 168, 'Actual partial literal pairs differ')
for number in range(169):
    start, end = events[number * 2:number * 2 + 2]
    require(type(start['forward_number']) is type(end['forward_number']) is int
            and start['forward_number'] == end['forward_number'] == number + 1, 'Actual native partial forward order differs')
    for key in ('request_id', 'warmup', 'batch_size', 'source_sha256', 'query_sha256', 'delivered_sha256', 'total_tokens', 'input_ids_sha256'):
        require(common.typed_equal(start[key], end[key]), 'Native partial start/completion input differs')
    require(type(end['batch_size']) is int and end['batch_size'] == 1 and end['warmup'] is (number == 0), 'Actual partial singleton/warmup scope differs')
    if number == 0:
        require(end['request_id'] == 'generic-warmup', 'Original generic warmup identity differs')
        built = old.literal_pair(tokenizer, old.WARMUP_QUERY, old.WARMUP_SOURCE)
        receipt = worker['owner']['ready']['warmup']
    else:
        pair = pairs[number - 1]
        require(pair['status'] == 'completed' and pair['request_id'] == end['request_id'], 'Partial native pair identity differs')
        built = old.literal_pair(tokenizer, pair['query'], pair['source'])
        require(pair['input_ids'] == built['input_ids'] and pair['input_ids_sha256'] == built['input_ids_sha256'], 'Actual partial tokenizer IDs differ')
        receipt = pair['receipt']
    for key, value in built.items():
        if key in ('input_ids', 'input_ids_sha256'):
            continue
        require(common.typed_equal(receipt['result'][key], value), 'Independent partial literal/token receipt differs')
    for key in ('source_sha256', 'query_sha256', 'delivered_sha256', 'total_tokens', 'input_ids_sha256'):
        require(common.typed_equal(end[key], built[key]), 'Actual partial native input hash differs')
    require(end['score'] == old.scalar(receipt), 'Actual partial native scalar arithmetic differs')
    require(all(type(receipt['counts'][key]) is int and receipt['counts'][key] == number + 1 for key in old.COUNTERS),
            'Actual perpair native prefix counters differ')
ops = worker['embedding_operations']
queries = {q['id']: q['query'] for q in plan['manifest']['queries']}
content = common.rows(STUDY / 'source-content.jsonl.gz')
documents = [common.document(r['path'], r['symbol'], r['source']) for r in content]
require(len(content) == len(set(documents)) == 1025, 'Initial genuine source document count differs')
wanted = [('documents', documents, None)] + [('query', [common.QUERY_PROMPT + queries[r['query_id']]], r['query_id']) for r in rows[:7]]
embedding_events = common.rows(STUDY / 'embedding-events.jsonl.gz')
common.verify_operations(ops, wanted, embedding_events, plan['embedding_checkpoint'], {sha(t.encode()): r['id'] for t, r in zip(documents, content, strict=True)})
require(len(ops) == 8 and ops[-1]['kind'] == 'query', 'Transport failed prefix has hidden embedding operations')
for r, operation in zip(rows[:7], ops[1:], strict=True):
    old.vector_capture(r['query_vector'])
    require(r['query_vector']['raw_sha256'] == operation['matrix_raw_sha256'], 'Partial actual query output differs')
for name, private_name in [('searches.jsonl.gz', 'searches.jsonl'), ('actual-http-attempts.jsonl.gz', 'actual-http-attempts.jsonl'), ('embedding-events.jsonl.gz', 'embedding-events.jsonl')]:
    require(gzip.decompress((STUDY / name).read_bytes()) == (work / private_name).read_bytes(), 'Partial public/raw journals differ')
native_dirs = [p for p in (work / 'qwen').glob('owned-*') if p.is_dir()]
require(len(native_dirs) == 1 and gzip.decompress((STUDY / 'native-forwards.jsonl.gz').read_bytes()) == (native_dirs[0] / 'forwards.jsonl').read_bytes()
        and not (native_dirs[0] / 'token.secret').exists(), 'Partial isolated native journal/token cleanup differs')
for pid in (22418, 22448):
    old.gone(pid)
require(parent['ownership']['pid'] == worker['pid'] == 22418 and worker['owner']['pid'] == 22448, 'Known owned groups differ')
for stop in (parent['cleanup'], worker['owner']['stop']):
    require(stop['owned_group_stopped'] is True and stop['forced_kill'] is False and stop['errors'] == [], 'Failed attempt native cleanup was forced/unproved')
require(worker['owner']['stop']['exit_code'] == 0 and worker['owner']['worker_stop']['clean_stop'] is True
        and worker['owner']['worker_stop']['listener_closed'] is True and worker['owner']['worker_stop']['auth_token_removed'] is True
        and worker['owner']['worker_stop']['auth_cleanup_error'] is None and worker['owner']['auth_token_removed'] is True
        and worker['owner'].get('private_cleanup_errors') == [], 'Isolated failed-prefix worker/auth cleanup differs')
for key in old.COUNTERS:
    require(type(worker['owner']['worker_stop']['counts'][key]) is int and worker['owner']['worker_stop']['counts'][key] == 169
            and worker['owner']['latest_status']['counts'][key] == 169, 'Actual final partial native counters differ')
require(worker['owner']['worker_stop']['counts']['mlx_active_memory_after_cleanup'] <= 4096, 'Native model allocations remain after failed prefix')
for key in ('http_thread_stopped', 'http_listener_closed', 'embedding_model_released', 'auth_token_absent', 'reranker_group_gone', 'reranker_listener_closed'):
    require(worker[key] is True, 'Partial clean release witness differs: ' + key)
require(worker['cleanup_errors'] == [] and worker['cleanup_signals']['received'] == []
        and worker['signals']['received'] == [] and parent['signals']['received'] == [], 'Partial cleanup/signal errors differ')
old.listener_closed(worker['http_url'])
old.listener_closed(worker['owner']['ready']['url'])
for path, value in parent['postexit_index_physical']['file_sha256'].items():
    require(common.file_sha(common.local_path(path)) == value, 'Failed prefix durable postexit physical database changed')
root = parent['ownership']
private = read(work / 'owned-process.json')
argv = [v.replace('<PROJECT>', str(ROOT)) for v in root['command']]
require(common.typed_equal(private, {**root, 'command': argv}) and root['command_sha256'] == sha(json.dumps(argv).encode()), 'Failed-prefix raw owned process proof differs')
record = {'failed_attempt_audit_passed': True, 'experiment_complete': False, 'experiment_qualified': False,
          'created_at_utc': datetime.now(timezone.utc).isoformat(), 'classification': 'Independently reconciled failed transport prefix and clean shutdown; no semantic quality evaluation/selection',
          'failed_study': str(STUDY.relative_to(ROOT)), 'original_plan_sha256': sha((STUDY / 'frozen-plan.json').read_bytes()),
          'parent_result_sha256': sha((STUDY / 'parent-result.json').read_bytes()), 'worker_result_sha256': sha((STUDY / 'worker-result.json').read_bytes()),
          'failure': worker['failure'], 'attempted_http_calls': 8, 'successful_http_calls': 7, 'successful_http_warmups': 3,
          'successful_measured_http_calls': 4, 'rejected_http_calls': 1, 'rejected_request_id_characters': len(rows[-1]['request_id']),
          'native_forward_starts': 169, 'native_forward_completions': 169, 'native_generic_warmup': 1, 'native_http_warmup_pairs': 72,
          'native_successful_measured_pairs': 96, 'genuine_initial_document_inputs': 1025, 'document_native_batches': 257,
          'successful_query_embedding_inputs': 7, 'query_native_forwards': 7, 'rejected_http_embedding_or_native_delta': 0,
          'all_partial_literal_inputs_independently_reencoded': True, 'all_actual_perpair_counts_reconciled': True,
          'all_raw_public_journals_equal': True, 'owned_groups_actual_gone': [22418, 22448], 'isolated_reranker_clean_exit': 0,
          'main_failed_worker_exit': 1, 'forced_kill': False, 'actual_auth_tokens_absent': True, 'actual_http_native_listeners_closed': True,
          'model_release_and_no_cleanup_errors': True, 'worker_lifecycle_passed_recorded': worker['lifecycle_passed'],
          'worker_lifecycle_flag_scope': 'False because original complete-study native_final_status was not reached; clean failure shutdown independently reconciled using actual stop/latest/ledger counts.',
          'new_model_calls': 0, 'new_retrieval_calls': 0, 'semantic_quality_fields_evaluated': False, 'quality_or_latency_distributions_selected': False,
          'corpus_partially_observed': True, 'new_untouched_holdout_claim': False,
          'continuation_requirement': 'Only original full138/135 query/pass/config schedule with shorter transport IDs, same gold/source/settings/scoring/packing, new output/work/database/model attempt paths; retain this original failure and do not reuse vector/scalar/query outputs.',
          'auditor_sha256': sha(Path(__file__).read_bytes()), 'frozen_original_independent_auditor_sha256': sha(raw)}
with (BASE / 'failed-prefix-audit.json').open('x') as out:
    out.write(json.dumps(record, indent=2, sort_keys=True) + '\n')
print(json.dumps({k: record[k] for k in ('failed_attempt_audit_passed', 'experiment_complete', 'successful_http_calls', 'native_forward_completions', 'owned_groups_actual_gone', 'semantic_quality_fields_evaluated')}))
