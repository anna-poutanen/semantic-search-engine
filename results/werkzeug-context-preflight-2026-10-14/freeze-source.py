"""Freeze pinned tracked Python source before labels; no Werkzeug/model import."""
import hashlib,json,subprocess,sys
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path.cwd();sys.path.insert(0,str(ROOT/'src'))
from semantic_search.corpus import extract_snippets, _source_files
from semantic_search.languages import extractor_fingerprint, decode_source
from semantic_search.source import physical_lines
REPO=ROOT/'.cache/corpora/werkzeug-context-holdout';OUT=ROOT/'results/werkzeug-context-preflight-2026-10-14'
REV='6389612fd1ee1bd93579eed5026e8fd471d04abd';TAG='aefbcabecec911dc6fa95791e50be264bf5e8415'
EXCLUSIONS=['**/*.js','**/*.jsx','**/*.mjs','**/*.cjs','**/*.ts','**/*.tsx','**/*.mts','**/*.cts']
def git(*args):return subprocess.check_output(['git','-C',str(REPO),*args],timeout=30)
def sha(b):return hashlib.sha256(b).hexdigest()
def canonical(x):return json.dumps(x,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
assert git('status','--porcelain=v1','--untracked-files=all')==b''
assert git('rev-parse','HEAD').decode().strip()==REV
assert git('rev-parse','3.1.3^{tag}').decode().strip()==TAG
assert git('rev-parse','3.1.3^{commit}').decode().strip()==REV
assert git('config','--get','remote.origin.url').decode().strip()=='https://github.com/pallets/werkzeug.git'
subprocess.run(['git','-C',str(REPO),'fsck','--strict'],check=True,capture_output=True)
assert subprocess.run(['git','check-ignore','-q',str(REPO)],cwd=ROOT).returncode==0
tracked=[b.decode() for b in git('ls-files','-z','--','src/werkzeug').split(b'\0') if b]
python_files=[p for p in tracked if p.endswith('.py')]
assert sorted(python_files)==[p.relative_to(REPO).as_posix() for p in _source_files(REPO,REPO/'src/werkzeug',EXCLUSIONS)]
sources={};files={};headers={}
for p in python_files:
 raw=(REPO/p).read_bytes();assert raw==git('show',f'{REV}:{p}');files[p]=sha(raw);sources[p]=decode_source(p,raw)
 lines=physical_lines(sources[p]);headers[p]={'physical_lines':len(lines),'first12_sha256':sha(''.join(lines[:12]).encode())}
windows=extract_snippets(REPO,'src/werkzeug',max_lines=160,overlap_lines=40,sources=sources,source_exclusions=EXCLUSIONS)
inventory=[]
for w in windows:
 assert w.text==''.join(physical_lines(sources[w.path])[w.start_line-1:w.end_line]) and w.content_hash==sha(w.text.encode())
 inventory.append({'id':w.id,'path':w.path,'symbol':w.symbol,'start_line':w.start_line,'end_line':w.end_line,'source_hash':w.content_hash})
assert len({w['id'] for w in inventory})==len(inventory)
license_raw=(REPO/'LICENSE.txt').read_bytes();assert license_raw==git('show',f'{REV}:LICENSE.txt')
(OUT/'WERKZEUG_LICENSE.txt').write_bytes(license_raw)
implementation={str(p.relative_to(ROOT)):sha(p.read_bytes()) for p in [ROOT/'src/semantic_search/corpus.py',ROOT/'src/semantic_search/languages.py',ROOT/'src/semantic_search/source.py']}
freeze={'version':1,'frozen_at_utc':datetime.now(timezone.utc).isoformat(),'labels_authored':False,'repo_url':'https://github.com/pallets/werkzeug','remote_origin':'https://github.com/pallets/werkzeug.git','historical_tag':'3.1.3','annotated_tag_object':TAG,'annotated_tag_type':git('cat-file','-t',TAG).decode().strip(),'annotated_tag_raw_sha256':sha(git('cat-file','-p',TAG)),'peeled_commit':REV,'revision':REV,'git_tree':git('rev-parse','HEAD^{tree}').decode().strip(),'git_object_integrity_fsck_strict':True,'pgp_signature_verified':False,'corpus':'.cache/corpora/werkzeug-context-holdout','source_root':'src/werkzeug','source_exclusions':EXCLUSIONS,'scope':'All clean tracked production Python files under src/werkzeug; tests, docs, JS/TS/assets and external packages excluded','git_clean':True,'cache_ignored':True,'all_tracked_scope_files':tracked,'tracked_python_source_files':files,'source_headers':headers,'source_file_count':len(files),'license':{'name':'BSD-3-Clause','path':'LICENSE.txt','sha256':sha(license_raw),'notice_path':'results/werkzeug-context-preflight-2026-10-14/WERKZEUG_LICENSE.txt'},'extractor':extractor_fingerprint(),'implementation_sha256':implementation,'source_window_settings':{'max_lines':160,'overlap_lines':40,'include_docstrings':True},'window_count':len(inventory),'window_inventory_sha256':sha(canonical(inventory)),'model_calls':0,'embedding_calls':0,'retrieval_calls':0,'werkzeug_runtime_imported':False,'prior_ranking_or_vectors_read':False,'generator_sha256':sha(Path(__file__).read_bytes())}
with (OUT/'source-freeze.json').open('x') as f:f.write(json.dumps(freeze,indent=2,sort_keys=True)+'\n')
with (OUT/'window-inventory.json').open('x') as f:f.write(json.dumps(inventory,indent=2,sort_keys=True)+'\n')
print(json.dumps({'revision':REV,'tree':freeze['git_tree'],'source_files':len(files),'windows':len(inventory),'source_freeze_sha256':sha((OUT/'source-freeze.json').read_bytes())}))
