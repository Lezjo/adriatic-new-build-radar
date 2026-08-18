from __future__ import annotations
import json,re
from collections import Counter,defaultdict
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlparse,parse_qsl,urlencode,urlunparse
ROOT=Path(__file__).resolve().parent; DATA=ROOT/'data'; DEBUG=DATA/'debug'; CURRENT=DATA/'current.json'; OBJECTS=DATA/'objects.json'; SOURCES=ROOT/'radar'/'sources.json'; OUT=DEBUG/'inventory_audit.json'; MD=DEBUG/'inventory_audit.md'
MANDATORY={'Immobiliare.it':['Jesolo','Caorle','Cavallino-Treporti','San Donà di Piave'],'Idealista':['Jesolo','San Donà di Piave','Cavallino-Treporti'],'Casa.it':['Jesolo','Cavallino-Treporti','San Donà di Piave'],'JBC':['Jesolo','Jesolo Paese',"Ca' Gamba",'Eraclea','Ponte di Piave','Fossalta di Piave','Noventa di Piave','San Donà di Piave']}
def load(p,d):
 try:return json.loads(p.read_text(encoding='utf-8'))
 except:return d
def canon(u):
 if not u:return ''
 try:
  p=urlparse(str(u));q=[(k,v) for k,v in parse_qsl(p.query) if k.lower() not in {'utm_source','utm_medium','utm_campaign','utm_content','utm_term'}]
  return urlunparse((p.scheme.lower(),p.netloc.lower(),p.path.rstrip('/'),'',urlencode(q),'')))
 except:return str(u)
def src(n):
 n=(n or '').lower()
 if 'immobiliare' in n:return 'Immobiliare.it'
 if 'idealista' in n:return 'Idealista'
 if 'casa.it' in n or n=='casa':return 'Casa.it'
 if 'jbc' in n:return 'JBC'
 return n or 'UNKNOWN'
def loc(v):return re.sub(r'\s+',' ',str(v or '').strip()).lower()
def url(r):return canon(r.get('source_url') or r.get('url') or r.get('listing_url'))
def rows():
 for p in (CURRENT,OBJECTS):
  d=load(p,{})
  if isinstance(d,dict):
   for k in ('objects','listings','items','rows','inventory'):
    if isinstance(d.get(k),list):return d[k]
 return []
def configured():
 d=load(SOURCES,{})
 out=[]
 if isinstance(d,dict):
  for l,ss in d.items():
   if isinstance(ss,list):
    for s in ss:
     if isinstance(s,dict):out.append({'location':str(l),'source':src(s.get('name')),'name':s.get('name'),'url':s.get('url'),'expected':s.get('expected_count',s.get('live_results',s.get('expected_results')))})
 return out
def counts(rs):
 bs=Counter();bp=Counter();us=defaultdict(set);up=defaultdict(set)
 for r in rs:
  s=src(r.get('source'));l=str(r.get('location') or 'Unknown');u=url(r);bs[s]+=1;bp[(l,s)]+=1
  if u:us[s].add(u);up[(l,s)].add(u)
 return {'by_source':dict(bs),'by_pair':{f'{l} | {s}':n for (l,s),n in sorted(bp.items())},'unique_urls_by_source':{s:len(v) for s,v in us.items()},'unique_urls_by_pair':{f'{l} | {s}':len(v) for (l,s),v in sorted(up.items())}}
def overlap(rs):
 o=defaultdict(set)
 for r in rs:
  u=url(r)
  if u:o[u].add(src(r.get('source')))
 x=[{'url':u,'sources':sorted(s)} for u,s in o.items() if len(s)>1]
 return {'urls_seen_in_multiple_sources':len(x),'examples':x[:200]}
def audit_coverage(cfg,rs):
 dbg=load(DEBUG/'capture_debug.json',[]); latest={}
 for x in dbg if isinstance(dbg,list) else []:latest[(str(x.get('location')),src(x.get('source')))]=x
 c=counts(rs)['unique_urls_by_pair'];out=[]
 for s in cfg:
  k=(s['location'],s['source']);d=latest.get(k,{});cap=int(d.get('records_captured') or c.get(f'{s["location"]} | {s["source"]}',0) or 0);pages=int(d.get('pages_captured') or 0);exp=s['expected'];pct=None
  if exp is not None:
   try:pct=round(cap/int(exp)*100,1)
   except:pass
  verdict='RED — capture error' if d.get('error') or (d.get('last_http_status') and int(d['last_http_status'])>=400) else ('RED — 0 records' if cap==0 else ('YELLOW — below declared inventory' if exp is not None and cap<int(exp) else 'GREEN — captured; live count not independently proven'))
  out.append({'location':s['location'],'source':s['source'],'configured_url':s['url'],'pages_captured':pages,'records_captured':cap,'expected_live_results':exp,'completeness_pct':pct,'http_status':d.get('last_http_status'),'error':d.get('error'),'verdict':verdict})
 for p in sorted(DEBUG.glob('*__jbc_manifest.json')):
  m=load(p,{})
  if isinstance(m,dict):out.append({'location':m.get('location'),'source':'JBC','configured_url':(m.get('start_urls') or [None])[0],'pages_captured':m.get('hub_pages_visited'),'records_captured':m.get('records_captured'),'expected_live_results':None,'completeness_pct':None,'http_status':m.get('last_http_status'),'error':'; '.join(m.get('errors',[])[:5]) or None,'verdict':'RED — 0 JBC records' if not m.get('records_captured') else 'GREEN — JBC discovery produced records; completeness requires live-source denominator','jbc_candidate_detail_urls':m.get('candidate_detail_urls'),'jbc_detail_pages_visited':m.get('detail_pages_visited'),'jbc_sitemap_urls_found':m.get('sitemap_urls_found'),'jbc_unit_records':m.get('unit_records'),'jbc_project_records':m.get('project_records')})
 return out
def gaps(cfg):
 p={(x['source'],loc(x['location'])) for x in cfg};g=[]
 for s,ls in MANDATORY.items():
  for l in ls:
   if (s,loc(l)) not in p:g.append({'source':s,'location':l,'issue':'Mandatory source/location is not configured in radar/sources.json'})
 return g
def report():
 rs=rows();cfg=configured();return {'schema_version':'inventory-audit-1.0','generated_at':datetime.now(timezone.utc).isoformat(),'status':'OK' if rs else 'NO_DATA','current_rows':len(rs),'unique_source_urls':len({url(r) for r in rs if url(r)}),'configured_source_locations':len(cfg),'coverage':audit_coverage(cfg,rs),'mandatory_configuration_gaps':gaps(cfg),'counts':counts(rs),'cross_source_overlap':overlap(rs),'methodology':{'important':['Captured count is NOT proof of complete live inventory.','Portal search-result totals must be measured independently from the collector.','Project, Unit and Listing are separate levels.','Same canonical URL across sources is overlap, not a new unique listing.','JBC is audited as a mandatory independent source layer.'],'next_upgrade':'Add independent live-result denominator extraction per portal/location.'}}
def md(r):
 z=['# Adriatic Radar — Inventory Audit','',f"Generated: `{r['generated_at']}`",'',f"Current rows: **{r['current_rows']}**",f"Unique source URLs: **{r['unique_source_urls']}**",'', '## Coverage','','| Location | Source | Pages | Captured | Expected live | Completeness | Verdict |','|---|---|---:|---:|---:|---:|---|']
 for x in r['coverage']:
  e='-' if x['expected_live_results'] is None else str(x['expected_live_results']);p='-' if x['completeness_pct'] is None else f"{x['completeness_pct']}%";z.append(f"| {x.get('location','')} | {x.get('source','')} | {x.get('pages_captured','-')} | {x.get('records_captured',0)} | {e} | {p} | {x.get('verdict','')} |")
 z+=['','## Mandatory configuration gaps',''];g=r['mandatory_configuration_gaps'];z += ['None detected.'] if not g else [f"- 🔴 **{x['source']} — {x['location']}**: {x['issue']}" for x in g]
 z+=['','## Current inventory by source','','| Source | Captured rows |','|---|---:|']+[f"| {s} | {n} |" for s,n in sorted(r['counts']['by_source'].items())]
 z+=['','## Cross-source overlap','',f"URLs present in multiple source layers: **{r['cross_source_overlap']['urls_seen_in_multiple_sources']}**",'','## Important','','This audit deliberately does not call a capture 100% complete merely because Playwright returned records. The next required step is an independent live-result denominator for each portal/location.']
 return '\n'.join(z)+'\n'
def main():
 r=report();DEBUG.mkdir(parents=True,exist_ok=True);OUT.write_text(json.dumps(r,ensure_ascii=False,indent=2),encoding='utf-8');MD.write_text(md(r),encoding='utf-8');print(json.dumps({'status':r['status'],'current_rows':r['current_rows'],'unique_source_urls':r['unique_source_urls'],'configured_source_locations':r['configured_source_locations'],'mandatory_gaps':len(r['mandatory_configuration_gaps']),'overlap_urls':r['cross_source_overlap']['urls_seen_in_multiple_sources'],'files':[str(OUT),str(MD)]},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
