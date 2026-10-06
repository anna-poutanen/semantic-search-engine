"""CPU-only literal-source/full-definition/160-40 eligibility gate, not semantic certification."""
import ast,hashlib,json,subprocess,sys
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path.cwd();sys.path.insert(0,str(ROOT/'src'))
from semantic_search.corpus import _Definitions, _source_files, extract_snippets
from semantic_search.languages import decode_source, extractor_fingerprint
from semantic_search.source import physical_lines
OUT=ROOT/'results/werkzeug-context-preflight-2026-10-14';REPO=ROOT/'.cache/corpora/werkzeug-context-holdout';DATA=ROOT/'benchmarks/werkzeug_context_queries.json'
def sha(b):return hashlib.sha256(b).hexdigest()
def canonical(x):return json.dumps(x,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
def git(*args):return subprocess.check_output(['git','-C',str(REPO),*args],timeout=30)
freeze_raw=(OUT/'source-freeze.json').read_bytes();f=json.loads(freeze_raw);data_raw=DATA.read_bytes();d=json.loads(data_raw)
assert f['labels_authored'] is False and all(f[k]==0 for k in ['model_calls','embedding_calls','retrieval_calls'])
assert d['source_freeze_sha256']==sha(freeze_raw) and d['source_root']==f['source_root'] and d['source_exclusions']==f['source_exclusions'] and d['revision']==f['revision']
assert git('status','--porcelain=v1','--untracked-files=all')==b''
assert git('rev-parse','HEAD').decode().strip()==f['revision']==f['peeled_commit']
assert git('rev-parse','HEAD^{tree}').decode().strip()==f['git_tree']
assert git('rev-parse','3.1.3^{tag}').decode().strip()==f['annotated_tag_object']
assert sha(git('cat-file','-p',f['annotated_tag_object']))==f['annotated_tag_raw_sha256']
paths=[p.relative_to(REPO).as_posix() for p in _source_files(REPO,REPO/f['source_root'],f['source_exclusions'])];assert paths==sorted(f['tracked_python_source_files']) and all(p.endswith('.py') for p in paths)
sources={};definitions={};lines={}
for p in paths:
 raw=(REPO/p).read_bytes();assert sha(raw)==f['tracked_python_source_files'][p] and raw==git('show',f"{f['revision']}:{p}")
 sources[p]=decode_source(p,raw);lines[p]=physical_lines(sources[p]);visitor=_Definitions();visitor.visit(ast.parse(sources[p]));definitions[p]=visitor.functions
windows=extract_snippets(REPO,f['source_root'],max_lines=160,overlap_lines=40,source_exclusions=f['source_exclusions'],sources=sources)
inventory=[]
for w in windows:
 text=''.join(lines[w.path][w.start_line-1:w.end_line]);assert text==w.text and sha(text.encode())==w.content_hash
 inventory.append({'id':w.id,'path':w.path,'symbol':w.symbol,'start_line':w.start_line,'end_line':w.end_line,'source_hash':w.content_hash})
assert len(inventory)==f['window_count']==1025 and len(paths)==f['source_file_count']==52
assert sha(canonical(inventory))==f['window_inventory_sha256']
assert inventory==json.loads((OUT/'window-inventory.json').read_text())
assert extractor_fingerprint()==f['extractor']
for p,h in f['implementation_sha256'].items():assert sha((ROOT/p).read_bytes())==h
license_raw=(REPO/'LICENSE.txt').read_bytes();assert license_raw==git('show',f"{f['revision']}:LICENSE.txt")==(OUT/'WERKZEUG_LICENSE.txt').read_bytes() and sha(license_raw)==f['license']['sha256']
proofs=[];positive=absent=required=multifile=0;ids=set()
for q in d['queries']:
 assert q['id'] not in ids;ids.add(q['id']);assert q['split']=='repository_holdout' and q['query'].strip() and q['notes'].strip()
 if q['kind']=='absent':assert q['targets']==[] and 'target_groups' not in q;absent+=1;proofs.append({'id':q['id'],'kind':'absent','notes':q['notes']});continue
 assert q['kind']=='present';positive+=1;groups=[];used_paths=set()
 for group in q['target_groups']:
  assert len(group)==1 # No unreviewed OR equivalence claims.
  required+=1;alternatives=[]
  for target in group:
   p,s=target['path'],target['symbol'];assert p in sources and p not in q['query'] and s not in q['query']
   matches=[n for name,n in definitions[p] if name==s];assert len(matches)==1;n=matches[0]
   assert target['definition_lines']==target['evidence_lines']==[n.lineno,n.end_lineno]
   assert type(n.lineno) is int and type(n.end_lineno) is int
   body=n.body[1:] if n.body and isinstance(n.body[0],ast.Expr) and isinstance(n.body[0].value,ast.Constant) and isinstance(n.body[0].value.value,str) else n.body
   assert body and not all(isinstance(x,ast.Pass) for x in body)
   raw=''.join(lines[p][n.lineno-1:n.end_lineno]).encode();assert sha(raw)==target['evidence_sha256'] and f['tracked_python_source_files'][p]==target['source_file_sha256']
   assert lines[p][n.lineno-1].strip()==target['declaration_anchor']
   eligible=[w.id for w in windows if w.path==p and w.start_line<=n.lineno<=n.end_lineno<=w.end_line];assert eligible
   alternatives.append(target|{'eligible_window_ids':eligible});used_paths.add(p)
  groups.append(alternatives)
 multifile+=len(used_paths)>1
 proofs.append({'id':q['id'],'kind':'present','required_groups':len(groups),'multi_file':len(used_paths)>1,'group_proofs':groups})
assert (len(d['queries']),positive,absent,required,multifile)==(15,12,3,34,8)
receipt={'version':1,'created_at_utc':datetime.now(timezone.utc).isoformat(),'checks_passed':True,'classification':'Source-only literal full-definition eligibility; independent semantic gold/absence review still required before inference','generator_sha256':sha(Path(__file__).read_bytes()),'artifact_sha256':{'benchmarks/werkzeug_context_queries.json':sha(data_raw),'results/werkzeug-context-preflight-2026-10-14/source-freeze.json':sha(freeze_raw),'results/werkzeug-context-preflight-2026-10-14/window-inventory.json':sha((OUT/'window-inventory.json').read_bytes()),'results/werkzeug-context-preflight-2026-10-14/WERKZEUG_LICENSE.txt':sha(license_raw)},'source_root':f['source_root'],'source_exclusions':f['source_exclusions'],'revision':f['revision'],'git_tree':f['git_tree'],'git_clean':True,'source_file_count':len(paths),'window_count':len(windows),'window_inventory_sha256':f['window_inventory_sha256'],'extractor':f['extractor'],'implementation_sha256':f['implementation_sha256'],'query_count':15,'positive_queries':12,'scoped_absent_queries':3,'required_groups':34,'multi_file_positive_queries':8,'or_alternative_groups':0,'query_proofs':proofs,'model_calls':0,'embedding_calls':0,'index_calls':0,'retrieval_calls':0,'werkzeug_runtime_imported':False,'ranking_outcomes_read':False,'semantic_review_required':True,'scope_limit':'Local executable behaviors and explicitly stated delegated callees, not every external algorithm or all possible application interactions'}
with (OUT/'eligibility.json').open('x') as f:f.write(json.dumps(receipt,indent=2,sort_keys=True)+'\n')
print(json.dumps({k:receipt[k] for k in ['checks_passed','source_file_count','window_count','query_count','required_groups','multi_file_positive_queries']}))
