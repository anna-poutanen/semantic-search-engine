"""CPU-only reconciliation of final narrative inputs; no producer/model calls."""
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import statistics

ROOT = Path.cwd()
OUT = ROOT / 'results/qwen-coding-final-synthesis-2026-10-14'
AUDIT = ROOT / 'results/qwen-coding-audit-final-2026-10-14'
STUDY = ROOT / 'results/qwen-coding-workflows-2026-10-14'
REVIEWS = ROOT / 'results/qwen-coding-patch-review-2026-10-14'
CONDITIONS = ('baseline', 'mcp_fast', 'mcp_qwen')

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def read(path):
    return json.loads(path.read_text())

def eq(a, b):
    assert type(a) is type(b), (type(a), type(b))
    if isinstance(a, dict):
        assert a.keys() == b.keys()
        for k in a: eq(a[k], b[k])
    elif isinstance(a, list):
        assert len(a) == len(b)
        for x, y in zip(a, b, strict=True): eq(x, y)
    else: assert a == b, (a, b)

paths = {AUDIT / 'independent-audit.json', STUDY / 'report.md', Path(__file__).resolve()}
cert = read(AUDIT / 'independent-audit.json')
assert cert['checks_passed'] is True and cert['inventory_complete'] is True
assert cert['final_completion_receipt'] is True and cert['recorded_attempts'] == cert['planned_attempts'] == 18
assert cert['missing_attempts'] == [] and cert['timing_all_qualified'] is True and cert['global_clock_ordered'] is True
for name, witness in cert['global_input_snapshots'].items():
    path = AUDIT / witness['file']; assert witness['file'] == 'as-of/' + name
    assert sha(path) == witness['sha256'] == cert['artifact_sha256'][name]; paths.add(path)
rows = read(AUDIT / 'as-of/results.json')
summary = read(AUDIT / 'as-of/summary.json')
assert len(rows) == len(cert['attempts']) == 18
outcomes = {a['run_id']: a for a in cert['attempts']}
assert len(outcomes) == 18
normal, pairing, exposures = {}, {}, []
for row in rows:
    public = STUDY / 'runs' / row['run_id']; path = public / 'result.json'; paths.add(path)
    assert sha(path) == outcomes[row['run_id']]['result_sha256']; eq(read(path), row)
    assert outcomes[row['run_id']]['integrity_passed'] is True
    normal[row['run_id']] = bool(row['completed'] and row['agent_exit_code'] == 0 and not row['failed'] and not row['timed_out'] and not row.get('infrastructure_error'))
    assert row['cleanup_clean'] is True and row['cleanup_qualified'] is True and row['lifecycle_qualified'] is True
    assert row['correct'] is (row['grade']['passed'] and row['compatibility']['passed'])
    assert row['completed_correct'] is (normal[row['run_id']] and row['correct'])
    assert outcomes[row['run_id']]['normally_completed'] is normal[row['run_id']]
    events_path = public / 'events.jsonl'; paths.add(events_path)
    assert sha(events_path) == row['artifact_sha256']['published']['events.jsonl']
    turns = [read_event['usage'] for read_event in [json.loads(line) for line in events_path.read_text().splitlines()] if read_event.get('type') == 'turn.completed']
    if turns:
        usage = {k: sum(t[k] for t in turns) for k in ['input_tokens','output_tokens','cached_input_tokens','cache_write_input_tokens','reasoning_output_tokens']}
        assert all(type(v) is int and v >= 0 for v in usage.values())
        assert usage['cached_input_tokens'] <= usage['input_tokens'] and usage['cache_write_input_tokens'] <= usage['input_tokens'] and usage['reasoning_output_tokens'] <= usage['output_tokens']
        usage['total_tokens'] = usage['input_tokens'] + usage['output_tokens']
        usage['uncached_input_tokens'] = usage['input_tokens'] - usage['cached_input_tokens']
        eq(usage, row['usage']); eq(usage, outcomes[row['run_id']]['provider_usage'])
    else: assert row['usage'] is None and outcomes[row['run_id']]['provider_usage'] is None
    pairing.setdefault((row['task_id'], row['repetition']), {})[row['condition']] = row
    patch = public / 'changes.patch'; paths.add(patch); assert sha(patch) == row['patch_sha256']
    if row['condition'] == 'baseline': assert outcomes[row['run_id']]['mcp']['successful'] == 0
    else:
        hp = public / 'http-receipts.jsonl'; paths.add(hp)
        assert sha(hp) == row['artifact_sha256']['published']['http-receipts.jsonl']
        calls = [json.loads(line) for line in hp.read_text().splitlines()]
        assert len(calls) == outcomes[row['run_id']]['mcp']['successful'] and not outcomes[row['run_id']]['mcp']['profile_overrides']
        exposures.append({'run_id':row['run_id'],'condition':row['condition'],'calls':[{'number':c['number'],'payload_tokens':c['response']['token_usage']['tokens'],'source_chars':sum(len(s['source']) for s in c['response']['results']),'backend_call_s':c['backend_call_s'],'spans':[{k:s[k] for k in ['path','symbol','start_line','end_line','source_hash','truncated']} for s in c['response']['results']]} for c in calls]})

assert len(pairing) == 6 and all(set(p) == set(CONDITIONS) for p in pairing.values())
def value(row, key):
    return row['usage'].get(key) if row['usage'] is not None and key in row['usage'] else row.get(key)
def stats(values):
    return {'observations':len(values),'sum':sum(values) if values else None,'median':statistics.median(values) if values else None}
for condition in CONDITIONS:
    subset = [r for r in rows if r['condition'] == condition]
    assert len(subset) == 6
    current = cert['conditions'][condition]
    for key in ['correct','completed_correct']:
        assert current[key] == sum(r[key] is True for r in subset)
    assert current['normally_completed'] == sum(normal[r['run_id']] for r in subset)
    assert current['timeouts'] == sum(r['timed_out'] is True for r in subset)
    assert current['infrastructure_errors'] == sum(bool(r.get('infrastructure_error')) for r in subset)
    for key, measurement in current['measurements'].items():
        eq(measurement, stats([value(r,key) for r in subset if value(r,key) is not None]))
        eq(measurement, summary['conditions'][condition]['measurements'][key])

compound = []
for comparison in cert['comparisons']:
    eligible = []
    for identity, pair in sorted(pairing.items()):
        a,b = pair[comparison['left']],pair[comparison['right']]
        if comparison['subset'] == 'normally_completed_pairs' and not (normal[a['run_id']] and normal[b['run_id']]): continue
        if comparison['subset'] == 'completed_correct_pairs' and not (a['completed_correct'] and b['completed_correct']): continue
        eligible.append((identity,a,b))
    assert len(eligible) == comparison['eligible_pairs']
    for key,m in comparison['measurements'].items():
        known = [(i,value(a,key),value(b,key)) for i,a,b in eligible if value(a,key) is not None and value(b,key) is not None]
        expected_members = [list(i) for i,_,_ in known]; eq(m['members'], expected_members)
        assert m['pairs'] == len(known)
        left,right = (sum(v for _,v,_ in known),sum(v for _,_,v in known)) if known else (None,None)
        assert m['left_sum'] == left and m['right_sum'] == right
        changes = [(b/a-1)*100 for _,a,b in known if a]
        assert m['ratio_pairs'] == len(changes)
        for actual,expected in [(m['aggregate_change_pct'],(right/left-1)*100 if left else None),(m['median_pair_change_pct'],statistics.median(changes) if changes else None)]:
            assert (actual is None and expected is None) or (actual is not None and expected is not None and abs(actual-expected) < 1e-12)
    members = comparison['measurements']['total_tokens']['members']
    delta = {key:0 for key in ['total_tokens','cached_input_tokens','uncached_input_tokens','output_tokens']}
    for task,rep in members:
        pair = pairing[(task,rep)]; a,b = pair[comparison['left']],pair[comparison['right']]
        for key in delta: delta[key] += b['usage'][key] - a['usage'][key]
    assert delta['total_tokens'] == delta['cached_input_tokens'] + delta['uncached_input_tokens'] + delta['output_tokens']
    compound.append({'left':comparison['left'],'right':comparison['right'],'subset':comparison['subset'],'known_usage_members':members,'right_minus_left_tokens':delta})
eq(cert['comparisons'],summary['comparisons'])

request_path = REVIEWS/'requests-six-trial-summary.json';click_path = REVIEWS/'click-six-trial-summary.json';star_path = REVIEWS/'starlette-review-summary.json'
paths.update([request_path,click_path,star_path])
requests,click,star = [read(p) for p in (request_path,click_path,star_path)]
for item in requests['inventory']:
    r = next(r for r in rows if r['run_id'] == item['run_id']); eq(item['usage'],r['usage']); assert item['frozen_correct'] is r['correct'] and item['completed_correct'] is r['completed_correct']
    for name, expected in item['evidence_sha256'].items():
        path=ROOT/name;assert sha(path)==expected;paths.add(path)
for name,expected in click['artifact_sha256'].items():
    path=REVIEWS/name;assert sha(path)==expected;paths.add(path)
for item in click['trials']:
    r=next(r for r in rows if r['run_id']==item['run_id']);assert item['public_result_sha256']==outcomes[r['run_id']]['result_sha256'];eq(item['frozen_outcomes'],{k:r[k] for k in item['frozen_outcomes']});assert item['frozen_feature']['failures']==r['grade']['detail']['failures']
for name,expected in star['artifact_sha256'].items():
    path=ROOT/name;assert sha(path)==expected;paths.add(path)
for item in star['runs']:
    r=next(r for r in rows if r['run_id']==item['run_id']);assert item['formal_correct_unchanged'] is r['correct'] and item['formal_completed_correct_unchanged'] is r['completed_correct']
    for name, expected in [(item['review_receipt_path'],item['review_receipt_sha256']),(item['source_review_path'],item['source_review_sha256'])]:
        path=ROOT/name;assert sha(path)==expected;paths.add(path)
assert star['totals']['raw_original_prospective_case_executions_passed']==52 and star['totals']['raw_original_prospective_case_executions_failed']==2
assert sum(r['supported_prospective_cases_passed'] for r in star['runs'])==42
assert sum(r['post_output_chunk_neutral_diagnostics_passed'] for r in star['runs'])==12
assert star['totals']['post_output_chunk_neutral_reference_case_executions_passed']==2
assert sum(r['completed_correct'] for r in rows)==6 and all(r['task_id']=='starlette_body_limits' for r in rows if r['completed_correct'])

reporter=ROOT/'scripts/report_qwen_workflows.py';paths.add(reporter)
spec=importlib.util.spec_from_file_location('final_report_render_identity',reporter);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
loaded=module.load_certified(STUDY,AUDIT/'independent-audit.json')
assert (STUDY/'report.md').read_text()==module.render(*loaded)
audit_source=ROOT/'scripts/audit_qwen_workflows.py';paths.add(audit_source);assert sha(audit_source)==cert['auditor_sha256']
for name,expected in cert['auditor_dependency_sha256'].items():
    path=ROOT/name;assert sha(path)==expected;paths.add(path)

accounting={'checks_passed':True,'classification':'Independent CPU final narrative/accounting reconciliation; no new grading or inference','observed_attempts':18,'completed_correct':6,'condition_accounting':cert['conditions'],'pair_accounting':cert['comparisons'],'exact_gross_decomposition':compound,'exposure':exposures,'source_review_totals':{'requests':6,'click':6,'starlette':6,'starlette_raw_original_probe_pass':52,'starlette_raw_original_probe_fail':2,'starlette_supported_prospective_pass':42,'starlette_post_output_candidate_pass':12,'starlette_post_output_reference_pass':2},'hosted_calls':0,'native_calls':0,'network_calls':0,'weight_bytes_read':False,'report_byte_identity_verified':True}
with (OUT/'independent-final-accounting.json').open('x') as f:f.write(json.dumps(accounting,indent=2,sort_keys=True)+'\n')
paths.add(OUT/'independent-final-accounting.json')
bind={'checked_at_utc':datetime.now(timezone.utc).isoformat(),'checks_passed':True,'classification':'Independent final source/metric/review bindings after complete certificate; immutable inputs only','source_sha256':{str(p.relative_to(ROOT)):sha(p) for p in sorted(paths)},'final_certificate_sha256':sha(AUDIT/'independent-audit.json'),'checks':['All18 terminal result objects/hashes, public completed-turn events, actual usage components and patch hashes.','All condition observations/sums/medians, independent normal/snapshot/complete-correct counts and every pair metric identity/sum/aggregate/median.','Exact gross decomposition uses identical known-usage pairs for cached, uncached and output components.','All three six-trial review summary bindings and retained Starlette prospective/post-output distinctions.','Committed final report bytes equal certified as-of rendering; auditor/dependency bytes unchanged.'],'native_calls':0,'hosted_calls':0,'network_calls':0,'production_environment_changes':False}
with (OUT/'source-bindings-final.json').open('x') as f:f.write(json.dumps(bind,indent=2,sort_keys=True)+'\n')
print(json.dumps({'checks_passed':True,'binding_sha256':sha(OUT/'source-bindings-final.json'),'completed_correct':6,'actual_mcp_calls':sum(len(x['calls']) for x in exposures),'real_qwen_forwards':sum(a['native_qwen']['completed'] for a in cert['attempts'])}))
