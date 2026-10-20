from __future__ import annotations
import argparse, copy, hashlib, importlib.util, json, math, os, re
from collections import Counter, defaultdict
from pathlib import Path
from semantic_search.corpus import Snippet
from semantic_search.context import compact_result, serialize_context
from semantic_search import context
from semantic_search.source import physical_lines
from semantic_search.relations import ReferenceGraph
from semantic_search.retrieval import tokenize, BM25Retriever

STOP=set('where is are the a an and or to of for in on at by with from when while does do as be it its into only that this then otherwise can without which each either any all before after while also through using return returns make made'.split())
def terms(text):
    result=set()
    for term in tokenize(text):
        if term in STOP or len(term)<3: continue
        if term.endswith('ies') and len(term)>4: term=term[:-3]+'y'
        elif term.endswith('s') and len(term)>3 and not term.endswith('ss'): term=term[:-1]
        result.add(term)
    return result

def score_groups(windows, groups):
    return [next((i for i,w in enumerate(windows,1) if any(w['path']==t['path'] and w['start_line']<=t['evidence_lines'][0]<=t['evidence_lines'][1]<=w['end_line'] for t in group)),None) for group in groups]

def diversity(query, pairs, features, idf, *, alpha=.35,beta=0.,path_bonus=0.,preserve=1,unique_first=True,prefix=0,skip_module=False):
    # Only generic query terms, immutable source features, and original ranking
    # enter this selector; no labels, query IDs, kinds, or benchmark metadata.
    candidate=[]; seen=set()
    for rank,(s,score) in enumerate(pairs):
        key=s.path,s.symbol
        if skip_module and rank and '<module>' in s.symbol: continue
        if unique_first and key in seen: continue
        seen.add(key); candidate.append((s,score,rank))
    q=terms(query)
    if prefix:
        q={t[:prefix] for t in q}
        features={key:{t[:prefix] for t in value} for key,value in features.items()}
    weights={term:idf.get(term,1.) for term in q}
    total=sum(weights.values()) or 1.
    selected=[]; counts=Counter(); keys=set(); paths=set()
    def add(row):
        selected.append(row); keys.add((row[0].path,row[0].symbol)); paths.add(row[0].path)
        counts.update(features[row[0].id]&q)
    for row in candidate[:preserve]:
        add(row)
    while len(selected)<8:
        choices=[]
        for row in candidate:
            s,score,rank=row
            if (s.path,s.symbol) in keys: continue
            matching=features[s.id]&q
            marginal=sum(weights[t]/(1+counts[t]) for t in matching)/total
            overlap=max((max(0,min(s.end_line,old.end_line)-max(s.start_line,old.start_line)+1)/min(s.end_line-s.start_line+1,old.end_line-old.start_line+1) for old,_,_ in selected if old.path==s.path),default=0.)
            rank_reward=math.exp(-rank/10.)
            utility=(1-alpha)*rank_reward+alpha*marginal-beta*overlap+path_bonus*(s.path not in paths)
            choices.append((utility,-rank,row))
        if not choices: break
        add(max(choices,key=lambda r:r[:2])[2])
    chosen={s.id for s,_,_ in selected}
    return [(s,score) for s,score,_ in selected]+[(s,score) for s,score in pairs if s.id not in chosen]

def literal_clauses(query):
    parts=[part.strip(' ?.,') for part in re.split(r',|\band\b|\bwhile\b|\bthen\b',query,flags=re.I)]
    clauses=[part for part in parts if len(terms(part))>=3]
    return clauses[:4] if len(clauses)>=2 else []

def lexical_extend(query,pairs,lexical,*,mode,preserve):
    clauses=[query] if mode=='full' else literal_clauses(query)
    if not clauses:return pairs
    base=[];seen=set()
    for s,score in pairs:
        if (s.path,s.symbol) not in seen:
            base.append((s,score));seen.add((s.path,s.symbol))
    expanded=list(base[:preserve]);seen={(s.path,s.symbol) for s,_ in expanded}
    clauses_rankings=[]
    for clause in clauses:
        ranking=[];local=set()
        for s,score in lexical.search(clause,limit=50):
            if (s.path,s.symbol) not in local:
                local.add((s.path,s.symbol));ranking.append((s,score))
        clauses_rankings.append(ranking)
    for depth in range(3):
        for ranking in clauses_rankings:
            if depth<len(ranking):
                s,score=ranking[depth]
                if (s.path,s.symbol) not in seen:
                    expanded.append((s,score));seen.add((s.path,s.symbol))
    for s,score in base:
        if (s.path,s.symbol) not in seen:
            expanded.append((s,score));seen.add((s.path,s.symbol))
    return expanded[:50]

def main():
    p=argparse.ArgumentParser();p.add_argument('--project-root',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    root=a.project_root.resolve()
    spec=importlib.util.spec_from_file_location('packing',root/'scripts/replay_context_packing.py');packing=importlib.util.module_from_spec(spec);spec.loader.exec_module(packing)
    frozen,preflight,rows,sources,_,_=packing.inputs(root)
    inventories={};graphs={};features={};idfs={};labels={};lexicals={}
    for dataset in preflight['datasets']:
        name=dataset['id'];snippets=[]
        for w in dataset['window_inventory']:
            text=''.join(physical_lines(sources[name][w['path']])[w['start_line']-1:w['end_line']])
            assert hashlib.sha256(text.encode()).hexdigest()==w['source_hash']
            snippets.append(Snippet(w['id'],w['path'],w['symbol'],w['start_line'],w['end_line'],text,w['source_hash']))
        inventories[name]={s.id:s for s in snippets}; graphs[name]=ReferenceGraph(sources[name],snippets);lexicals[name]=BM25Retriever(snippets)
        features[name]={s.id:terms(s.symbol+'\n'+s.text) for s in snippets}
        df=Counter(t for ts in features[name].values() for t in ts)
        idfs[name]={t:math.log(1+(len(snippets)-c+.5)/(c+.5)) for t,c in df.items()}
        labels[name]={q['id']:q for q in json.loads((root/f'benchmarks/{name}_language_queries.json').read_text())['queries']}
    configs={'baseline':None}
    for mode in ('full','clause'):
        for preserve in (1,3):
            configs[f'{mode}-lexical-p{preserve}']={'mode':mode,'preserve':preserve}
    results={};cache={};baseline_exact=0
    for name,config in configs.items():
        out=[]
        for row in rows:
            ds=row['dataset']; pairs=[(inventories[ds][w['id']],w['retrieval_score']) for w in row['candidate_windows']]
            query=row['response']['query'];template={k:copy.deepcopy(row['response'][k]) for k in ('query','scope','match_status')}
            if config is not None:pairs=lexical_extend(query,pairs,lexicals[ds],**config)
            stream=packing.ordered_stream(pairs,graphs[ds],baseline=True)
            key=(ds,query,tuple(s.id for s,_,_,_ in stream))
            if key not in cache:cache[key]=packing.baseline_response(template,stream,context,physical_lines)
            response=cache[key]
            if name=='baseline':
                assert serialize_context(compact_result(response))==serialize_context(compact_result(row['response'])), row['query_id']
                baseline_exact+=1
            label=labels[ds][row['query_id']];groups=[] if row['kind']=='absent' else label.get('target_groups',[label.get('targets')])
            ranks=score_groups(response['results'],groups)
            out.append({'dataset':ds,'condition':row['condition'],'query_id':row['query_id'],'kind':row['kind'],'multi':row['multi_file_and'],'ranks':ranks,'complete':bool(groups) and all(r is not None for r in ranks),'groups':sum(r is not None for r in ranks),'required':len(groups),'tokens':response['token_usage']['tokens'],'rows':[r['id'] for r in response['results']]})
        summaries={}
        for ds in inventories:
            for cond in ('dense','hybrid','bm25'):
                rr=[r for r in out if r['dataset']==ds and r['condition']==cond and r['kind']=='present']
                summaries[ds+'/'+cond]={'complete':sum(r['complete'] for r in rr),'n':len(rr),'groups':sum(r['groups'] for r in rr),'required':sum(r['required'] for r in rr),'multi':sum(r['complete'] for r in rr if r['multi']),'multi_n':sum(r['multi'] for r in rr)}
        base=results.get('baseline',{}).get('cases',[])
        deltas=[{'dataset':r['dataset'],'condition':r['condition'],'query_id':r['query_id'],'before':b['ranks'],'after':r['ranks']} for r,b in zip(out,base) if r['ranks']!=b['ranks']]
        results[name]={'config':config,'summary':summaries,'deltas':deltas,'cases':out}
        print(json.dumps({'name':name,'summary':summaries,'changed':len(deltas)}),flush=True)
    a.output.write_text(json.dumps({'baseline_exact':baseline_exact,'prototype_only':True,'models':0,'retrieval':'CPU Python BM25 corpus clause/full-query retrieval plus immutable saved base ranks; changes candidate pool','results':results},indent=2)+'\n')
if __name__=='__main__':main()
