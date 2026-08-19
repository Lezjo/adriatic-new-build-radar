from __future__ import annotations
import hashlib, json, re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse, quote_plus
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright

ROOT=Path(__file__).resolve().parents[2]
DATA=ROOT/"data"; DEBUG=DATA/"debug"
SOURCES=json.loads((ROOT/"radar"/"sources.json").read_text(encoding="utf-8"))
COLLECTOR_VERSION="portal-capture-v13.0"

PRICE_RE=re.compile(r"(?:€\s*)?([\d\.,]+)\s*(k)?|([\d\.,]+)\s*k?\s*€",re.I)
AREA_RE=re.compile(r"(?:superficie|superficie commerciale|mq|m²|m2)\s*[:\-]?\s*(\d{2,4}(?:[.,]\d+)?)|\b(\d{2,4}(?:[.,]\d+)?)\s*m(?:²|2)\b",re.I)
ROOM_RE=re.compile(r"\b(\d+)\s*(?:camere|stanze|locali|rooms?)\b",re.I)
BED_RE=re.compile(r"\b(\d+)\s*(?:camere\s*da\s*letto|camere|bedrooms?)\b",re.I)
FLOOR_RE=re.compile(r"(?:piano|floor)\s*(?:numero\s*)?([A-Za-z0-9°\-]+)",re.I)
ENERGY_RE=re.compile(r"(?:classe\s*energetica|classe|energia)\s*[:\-]?\s*(A4|A3|A2|A1|A|B|C|D|E|F|G)\b",re.I)
UNIT_RE=re.compile(r"\b(?:unità|unita|interno|lotto|unit)\s*[:#]?\s*([A-Z]?\d+(?:[.\-]\d+)?)\b",re.I)

BAD=("/login","/registr","/contatti","/contact","/privacy","/cookie","/agenzie","/agenzia","/search","/ricerca","/map","/mappa","/faq","/blog")
ASSET=(".jpg",".jpeg",".png",".gif",".webp",".svg",".pdf",".css",".js",".xml",".ico")
HINTS=("/annunci/","/immobili/","/immobile/","/case/","/appartamenti/","/ville/","/residenze/","/residence/","/nuove-costruzioni/","/nuove_costruzioni/","/vendita/","/vendita-case/","/vendita-nuove","/nuova-costruzione/","/project/","/progetti/")
PROMO=("ribassato","ribasso","prezzo precedente","sconto","offerta","promozione","promo","occasione","prezzo speciale","ultimo prezzo","ridotto")
FEATURES={"parking":("posto auto","parcheggio","parking"),"garage":("garage","box auto","autorimessa"),"pool":("piscina",),"elevator":("ascensore","elevatore"),"pv_present":("pannelli fotovoltaici","fotovoltaico","fotovoltaica","impianto fotovoltaico","pannelli solari"),"heat_pump":("pompa di calore","pompe di calore"),"sea_view":("vista mare","vista sul mare","fronte mare"),"terrace":("terrazza","terrazzo"),"garden":("giardino","verde privato"),"ev_charging":("wallbox","colonnina di ricarica","ricarica elettrica"),"air_conditioning":("aria condizionata","climatizzazione")}
LOC=(
("cavallino-treporti",("cavallino-treporti","cavallino treporti","ca' savio","ca savio","ca' vio","ca vio","punta sabbioni","treporti")),
("san-dona-di-piave",("san donà di piave","san dona di piave","san dona")),
("caorle",("caorle","porto santa margherita","lido altanea","duna verde")),
("treviso",("treviso","santa maria del rovere","selvana","monigo","canizzano","sant'antonino","san zeno","fiera")),
("jesolo",("jesolo","jesolo lido","lido di jesolo","jesolo paese","ca' gamba","ca gamba","cortellazzo","piazza mazzini","piazza brescia","piazza trieste","piazza drago","piazza nember","faro di jesolo")),
)
MICRO=(
("jesolo","Jesolo Paese",("jesolo paese","centro storico")),("jesolo","Lido di Jesolo",("lido di jesolo","jesolo lido")),
("jesolo","Ca' Gamba",("ca' gamba","ca gamba")),("jesolo","Cortellazzo",("cortellazzo",)),("jesolo","Piazza Nember / Faro",("piazza nember","faro di jesolo")),
("jesolo","Pineta",("pineta",)),("jesolo","Piazza Mazzini",("piazza mazzini",)),("jesolo","Piazza Brescia",("piazza brescia",)),("jesolo","Piazza Trieste",("piazza trieste",)),("jesolo","Piazza Drago",("piazza drago",)),
("cavallino-treporti","Ca' Savio",("ca' savio","ca savio")),("cavallino-treporti","Ca' Vio",("ca' vio","ca vio")),("cavallino-treporti","Punta Sabbioni",("punta sabbioni",)),("cavallino-treporti","Treporti",("treporti",)),("cavallino-treporti","Cavallino",("cavallino",)),
("san-dona-di-piave","Mussetta",("mussetta",)),("san-dona-di-piave","Calvecchia",("calvecchia",)),("san-dona-di-piave","Fiorentina",("fiorentina",)),
("treviso","Santa Maria del Rovere",("santa maria del rovere",)),("treviso","Selvana",("selvana",)),("treviso","Monigo",("monigo",)),("treviso","Canizzano",("canizzano",)),("treviso","Sant'Antonino",("sant'antonino",)),
("caorle","Porto Santa Margherita",("porto santa margherita",)),("caorle","Lido Altanea",("lido altanea",)),("caorle","Duna Verde",("duna verde",)),("caorle","Brussa",("brussa",)),("caorle","Ponente",("ponente",)),("caorle","Levante",("levante",)),
)

def now(): return datetime.now(timezone.utc).isoformat().replace("+00:00","Z")
def canon(u):
 p=urlparse(u); q=[(k,v) for k,v in parse_qsl(p.query,keep_blank_values=True) if k.lower() not in {"utm_source","utm_medium","utm_campaign","utm_content","utm_term","fbclid","gclid"}]
 return urlunparse((p.scheme.lower(),p.netloc.lower(),p.path.rstrip("/"),"",urlencode(q),""))
def host(u): return urlparse(u).netloc.lower().removeprefix("www.")
def norm(s): return " ".join((s or "").replace("\xa0"," ").split())
def parse_num(s):
 try:
  x=s
  if "." in x and "," in x: x=x.replace(".","").replace(",",".")
  elif "," in x: x=x.replace(",", "") if len(x.rsplit(",",1)[1])==3 else x.replace(",",".")
  elif "." in x and len(x.rsplit(".",1)[1])==3: x=x.replace(".","")
  return float(x)
 except: return None
def price(t):
 m=PRICE_RE.search(t or "")
 if not m:return None
 n=next((x for x in m.groups() if x and re.search(r"\d",x)),None)
 if not n:return None
 v=parse_num(n)
 k=any(isinstance(x,str) and x.lower()=="k" for x in m.groups())
 v=v*(1000 if k else 1) if v is not None else None
 return int(v) if v and 10000<=v<=10000000 else None
def area(t):
 m=AREA_RE.search(t or ""); 
 if not m:return None
 v=parse_num(m.group(1) or m.group(2)); return v if v and 15<=v<=5000 else None
def bedrooms(t):
 m=BED_RE.search(t or ""); return int(m.group(1)) if m else None
def rooms(t):
 m=ROOM_RE.search(t or ""); return int(m.group(1)) if m else None
def floor(t):
 m=FLOOR_RE.search(t or ""); return m.group(1) if m else None
def energy(t):
 m=ENERGY_RE.search(t or ""); return m.group(1).upper() if m else None
def unit(t):
 m=UNIT_RE.search(t or ""); return m.group(1).upper() if m else None
def location(text,fallback="",url="",title=""):
 u=(url or "").lower(); t=norm(title).lower(); b=norm(text).lower()
 for loc,terms in LOC:
  if any(x in u for x in terms): return loc
 for loc,terms in LOC:
  if any(x in t for x in terms): return loc
 for loc,terms in LOC:
  if any(x in b for x in terms): return loc
 return fallback
def micro(text,loc):
 low=(text or "").lower()
 for l,m,terms in MICRO:
  if l==loc and any(x in low for x in terms): return m,"VERIFIED_TEXT",.9
 return None,"UNVERIFIED",0.0
def feats(text):
 low=(text or "").lower(); return [k for k,terms in FEATURES.items() if any(x in low for x in terms)]
def status(text):
 low=(text or "").lower()
 if any(x in low for x in ("pre-lancio","prelaunch","prossima costruzione","in progetto")): return "PRE_LAUNCH"
 if any(x in low for x in ("cantiere","lavori in corso","in costruzione","costruzione in corso")): return "UNDER_CONSTRUCTION"
 if any(x in low for x in ("progetto approvato","permesso di costruire","pua")): return "PLANNED"
 return "ACTIVE"
def bad(u): return any(x in urlparse(u).path.lower() for x in BAD) or urlparse(u).path.lower().endswith(ASSET)
def listing_url(u,base):
 if host(u)!=host(base) or bad(u): return False
 p=urlparse(u).path.lower()
 if not p or p=="/": return False
 return any(x in p for x in HINTS) or (len(p.rstrip("/").split("/")[-1])>=16 and "-" in p)
def page_url(u,base):
 if host(u)!=host(base) or bad(u): return False
 p=urlparse(u); q=dict(parse_qsl(p.query)); return any(x in q for x in ("page","pagina","p","pag")) or bool(re.search(r"/page[-/]?\d+",p.path.lower()))
def anchors(page):
 try:return page.locator("a[href]").evaluate_all("""els=>els.map(e=>({href:e.getAttribute('href')||'',text:(e.innerText||e.getAttribute('aria-label')||e.getAttribute('title')||'').trim()}))""")
 except:return []
def body(page):
 try:return norm(page.locator("body").inner_text(timeout=5000))
 except:return ""
def title(page,u):
 try:t=norm(page.title() or "")
 except:t=""
 if t:return t[:300]
 return norm(u.rstrip("/").rsplit("/",1)[-1].replace("-"," "))[:300]
def jsonld(page):
 out=[]
 try:
  for n in page.locator('script[type="application/ld+json"]').all():
   try:
    v=json.loads(n.text_content(timeout=1000) or ""); out.extend(v if isinstance(v,list) else [v])
   except: pass
 except: pass
 return out
def meta(page):
 out=[]
 for sel in ("meta[property='og:title']","meta[property='og:description']","meta[name='description']"):
  try:
   x=page.locator(sel).first
   if x.count():
    v=x.get_attribute("content")
    if v:out.append(norm(v))
  except:pass
 return out
def extract(page,source,fallback,u,run,evidence="",method="browser"):
 b=body(page); ti=title(page,u); text=norm(" ".join(meta(page)+[ti,b,evidence]))
 loc=location(text,fallback,u,ti); ml,vs,vc=micro(text,loc); fs=feats(text)
 pr=None
 for n in jsonld(page):
  if isinstance(n,dict):
   o=n.get("offers")
   try:
    if isinstance(o,dict) and o.get("price") is not None: pr=int(float(str(o["price"]).replace(",","."))); break
    if n.get("price") is not None: pr=int(float(str(n["price"]).replace(",","."))); break
   except:pass
 pr=pr or price(text)
 rid=hashlib.sha1(canon(u).encode()).hexdigest()
 raw=DATA/"raw"/run/f"{rid}.html"; captured=False
 try: raw.parent.mkdir(parents=True,exist_ok=True); raw.write_text(page.content(),encoding="utf-8"); captured=True
 except: pass
 old=None
 mm=re.search(r"(?:prezzo precedente|anziché|anziche).*?([\d\.,]+)\s*€",text,re.I)
 if mm: old=price(mm.group(1)+" €")
 lid="listing:"+rid
 pk=re.sub(r"\b(?:appartamento|trilocale|quadrilocale|bilocale|attico|villa|villetta)\b.*","",ti,flags=re.I).strip().lower() or ti.lower()
 pid="candidate-project:"+hashlib.sha1(f"{loc}|{pk}".encode()).hexdigest()
 return {"source":source,"source_run_id":run,"source_url":canon(u),"listing_id":lid,"listing_title":ti,"location":loc,"micro_location":ml,"macro_zone":loc,"location_verification_status":vs,"location_verification_confidence":vc,"project_id_candidate":pid,"unit_id":unit(text),"record_type":"UNIT" if unit(text) or bedrooms(text) is not None or rooms(text) is not None else "PROJECT","status":status(text),"price":pr,"old_price":old,"area_m2":area(text),"rooms":rooms(text),"bedrooms":bedrooms(text),"floor":floor(text),"energy_class":energy(text),"features":fs,"parking":"parking" in fs,"garage":"garage" in fs,"terrace":"terrace" in fs,"pool":"pool" in fs,"pv_present":"pv_present" in fs,"heat_pump":"heat_pump" in fs,"ev_charging":"ev_charging" in fs,"sea_view":"sea_view" in fs,"discount_signal":bool(old),"discount_keywords":[],"promotion_text":None,"promotion":{"detected":bool(old),"old_price":old,"new_price":pr,"amount":old-pr if old and pr and old>pr else None,"percent":round((old-pr)/old*100,2) if old and pr and old>pr else None,"evidence_terms":[]},"raw_text":text[:16000],"raw_artifact":f"data/raw/{run}/{rid}.html" if captured else None,"raw_capture":captured,"capture_method":method,"captured_at":now()}
def context(browser,referer=None):
 h={"Accept-Language":"it-IT,it;q=0.9,en;q=0.7","Accept":"text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8","Upgrade-Insecure-Requests":"1"}
 if referer:h["Referer"]=referer
 c=browser.new_context(locale="it-IT",user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36",extra_http_headers=h); c.set_default_timeout(12000); return c
def goto(page,u):
 r=page.goto(u,wait_until="domcontentloaded",timeout=35000)
 try:page.wait_for_load_state("networkidle",timeout=6000)
 except:pass
 return r.status if r else None
def candidates(page,base):
 cs={}; ps=[]; seen=set()
 for r in anchors(page):
  h=str(r.get("href") or "").strip()
  if not h or h.startswith(("javascript:","mailto:","tel:","#")):continue
  u=canon(urljoin(base,h))
  if u in seen:continue
  seen.add(u)
  if page_url(u,base):ps.append(u)
  elif listing_url(u,base):cs.setdefault(u,norm(str(r.get("text") or "")))
 return cs,ps
def search_queries(source,loc):
 domain="immobiliare.it" if "immobiliare" in source else "idealista.it" if "idealista" in source else "casa.it"
 place={"jesolo":"Jesolo Venezia","caorle":"Caorle Venezia","cavallino-treporti":"Cavallino Treporti Venezia","san-dona-di-piave":"San Dona di Piave Venezia","treviso":"Treviso"}.get(loc,loc.replace("-"," "))
 return [f"site:{domain} {place} nuove costruzioni appartamento",f"site:{domain} {place} nuova costruzione trilocale",f"site:{domain} {place} nuova costruzione quadrilocale"]
def search_fallback(browser,loc,spec,run,debug):
 source=spec["name"]; target=host(spec["url"]); c=context(browser); p=c.new_page(); discovered={}; errors=[]; pages=0
 for q in search_queries(source,loc):
  for engine in ("https://www.google.com/search?q={}&num=100","https://www.bing.com/search?q={}&count=50"):
   try:
    s=goto(p,engine.format(quote_plus(q))); pages+=1
    if s and s>=400:continue
    for r in anchors(p):
     h=str(r.get("href") or "").strip()
     if not h:continue
     u=canon(h)
     if host(u)==target and listing_url(u,spec["url"]):discovered.setdefault(u,norm(str(r.get("text") or "")))
    if discovered:break
   except Exception as e:errors.append(f"search:{type(e).__name__}:{e}")
 c.close()
 rows=[]; rej=[]
 for u,evidence in list(discovered.items())[:int(spec.get("fallback_max_results",100))]:
  c2=context(browser); p2=c2.new_page()
  try:
   s=goto(p2,u)
   if s and s<400:rows.append(extract(p2,source,loc,u,run,method="browser_fallback"))
   else:
    # Keep URL even when detail remains blocked; this is an auditable discovery,
    # not a fabricated full listing.
    rows.append(extract(p2,source,loc,u,run,evidence=evidence,method="search_result_only"))
  except Exception as e:rej.append({"url":u,"reason":f"fallback_detail:{type(e).__name__}"})
  finally:c2.close()
 report={"source":source,"location":loc,"source_run_id":run,"search_url":spec["url"],"collector":COLLECTOR_VERSION,"parser":source_parser(source),"capture_method":"search_fallback","pages_visited":pages,"records_seen":len(discovered),"records_parsed":len(rows),"records_normalized":len(rows),"records_published":len(rows),"records_rejected":len(rej),"rejection_reasons":reason_counts(rej),"raw_capture":sum(x.get("raw_capture",False) for x in rows),"manifest":True,"status":"PARTIAL" if rows else "BROKEN","coverage":"PARTIAL" if rows else "MISSING","errors":errors[:50],"candidate_urls":list(discovered)[:500],"rejected":rej[:500]}
 write_debug(debug,source,loc,report); return rows,report
def source_parser(source):
 s=source.lower()
 return "jbc" if s=="jbc_direct" else "immobiliare" if "immobiliare" in s else "idealista" if "idealista" in s else "casa" if "casa" in s else "generic"
def reason_counts(items):
 d={}
 for x in items:
  r=x.get("reason","unknown"); d[r]=d.get(r,0)+1
 return d
def write_debug(debug,source,loc,report):
 debug.mkdir(parents=True,exist_ok=True); (debug/f"coverage_{re.sub(r'[^a-zA-Z0-9._-]+','_',loc)}_{re.sub(r'[^a-zA-Z0-9._-]+','_',source)}.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
def generic(browser,loc,spec,run,debug):
 source=spec["name"]; start=canon(spec["url"]); c=context(browser); p=c.new_page(); q=[start]; queued={start}; visited=set(); cs={}; rej=[]; errs=[]; pages=0; statuses=[]; blocked=False
 while q and pages<int(spec.get("max_pages",100)):
  u=q.pop(0)
  if u in visited:continue
  visited.add(u)
  try:
   s=goto(p,u); pages+=1; statuses.append(s)
   if s in (401,403,429):blocked=True;break
   if s and s>=400:continue
   found,nexts=candidates(p,u)
   cs.update({k:v for k,v in found.items() if k not in cs})
   for n in nexts:
    if n not in queued and len(queued)<int(spec.get("max_pages",100))*2:queued.add(n);q.append(n)
  except Exception as e:errs.append(f"page:{type(e).__name__}:{e}")
 rows=[]
 if not blocked:
  for u in list(cs):
   try:
    s=goto(p,u)
    if s in (401,403,429):blocked=True;break
    if s and s>=400:rej.append({"url":u,"reason":f"http_{s}"});continue
    rows.append(extract(p,source,loc,u,run))
   except Exception as e:rej.append({"url":u,"reason":f"detail:{type(e).__name__}"})
 c.close()
 if blocked or not rows:
  fb,fr=search_fallback(browser,loc,spec,run,debug); rows.extend(fb)
 report={"source":source,"location":loc,"source_run_id":run,"search_url":start,"collector":COLLECTOR_VERSION,"parser":source_parser(source),"capture_method":"browser_then_search_fallback" if blocked or not rows else "browser","pages_visited":pages,"records_seen":len(cs),"records_parsed":len(rows),"records_normalized":len(rows),"records_published":len(rows),"records_rejected":len(rej),"rejection_reasons":reason_counts(rej),"raw_capture":sum(x.get("raw_capture",False) for x in rows),"manifest":True,"status":"PASS" if rows and not blocked else "PARTIAL" if rows else "BROKEN","coverage":"PASS" if rows and not blocked else "PARTIAL" if rows else "MISSING","blocked":blocked,"http_statuses":statuses,"errors":errs[:50],"candidate_urls":list(cs)[:500],"rejected":rej[:500]}
 write_debug(debug,source,loc,report); return rows,report
def jbc(browser,specs,run,debug):
 source="jbc_direct"; spec=specs[0]; home=canon(spec.get("url") or "https://www.jbcimmobiliare.it/"); c=context(browser);p=c.new_page();q=[home,"https://www.jbcimmobiliare.it/nuove-costruzioni/"];queued=set(q);seen=set();details={};errs=[];rej=[];pages=0;statuses=[]
 def prop(u,label):
  if host(u)!="jbcimmobiliare.it" or bad(u):return False
  path=urlparse(u).path.strip("/").lower(); hay=path+" "+label.lower()
  return bool(path and len(path.split("/")[-1])>=18 and (path.count("-")>=2 or any(x in hay for x in ("appartamento","trilocale","quadrilocale","bilocale","attico","villa","residenza","nuovo","progetto"))))
 while q and pages<int(spec.get("max_hub_pages",30)):
  u=q.pop(0)
  if u in seen:continue
  seen.add(u)
  try:
   s=goto(p,u);pages+=1;statuses.append(s)
   if s and s>=400:continue
   for r in anchors(p):
    h=str(r.get("href") or "").strip(); lab=norm(str(r.get("text") or ""))
    if not h or h.startswith(("javascript:","mailto:","tel:","#")):continue
    a=canon(urljoin(u,h))
    if host(a)!="jbcimmobiliare.it" or bad(a):continue
    if prop(a,lab):details.setdefault(a,lab);continue
    path=urlparse(a).path.lower()
    if any(x in path for x in ("immobili","vendita","cantiere","cantieri","progetti","progetto","nuove-costruzioni")) and a not in queued and len(queued)<int(spec.get("max_hub_pages",30))*2:queued.add(a);q.append(a)
  except Exception as e:errs.append(f"hub:{type(e).__name__}:{e}")
 rows=[]
 for u in list(details)[:int(spec.get("max_detail_pages",300))]:
  try:
   s=goto(p,u)
   if s and s>=400:rej.append({"url":u,"reason":f"http_{s}"});continue
   r=extract(p,source,"",u,run); 
   if r.get("location"):rows.append(r)
   else:rej.append({"url":u,"reason":"location_unresolved"})
  except Exception as e:rej.append({"url":u,"reason":f"detail:{type(e).__name__}"})
 c.close(); counts={}
 for r in rows:counts[r.get("location","unknown")]=counts.get(r.get("location","unknown"),0)+1
 report={"source":source,"location":"GLOBAL","source_run_id":run,"search_url":home,"collector":COLLECTOR_VERSION,"parser":"jbc_global","capture_method":"browser","pages_visited":pages,"records_seen":len(details),"records_parsed":len(rows),"records_normalized":len(rows),"records_published":len(rows),"records_rejected":len(rej),"rejection_reasons":reason_counts(rej),"raw_capture":sum(x.get("raw_capture",False) for x in rows),"manifest":True,"status":"PASS" if rows else "BROKEN","coverage":"PASS" if rows else "MISSING","http_statuses":statuses,"location_counts":counts,"errors":errs[:50],"candidate_urls":list(details)[:1000],"rejected":rej[:500]}
 write_debug(debug,source,"GLOBAL",report);return rows,report
def run():
 started=now(); run_id=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ"); debug=DEBUG/"capture"/run_id; debug.mkdir(parents=True,exist_ok=True); all_rows=[]; coverage=[]; done=False
 with sync_playwright() as pw:
  browser=pw.chromium.launch(headless=True)
  try:
   js=[s for specs in SOURCES.values() for s in specs if s.get("name")=="jbc_direct"]
   for loc,specs in SOURCES.items():
    for spec in specs:
     name=spec.get("name","unknown")
     try:
      if name=="jbc_direct":
       if done:continue
       rows,rep=jbc(browser,js,run_id,debug);done=True
      else: rows,rep=generic(browser,loc,spec,run_id,debug)
      all_rows.extend(rows);coverage.append(rep)
     except Exception as e:
      coverage.append({"source":name,"location":loc,"source_run_id":run_id,"search_url":spec.get("url"),"collector":COLLECTOR_VERSION,"parser":source_parser(name),"pages_visited":0,"records_seen":0,"records_parsed":0,"records_normalized":0,"records_published":0,"records_rejected":0,"rejection_reasons":{f"collector_error:{type(e).__name__}":1},"raw_capture":0,"manifest":False,"status":"BROKEN","coverage":"BROKEN","errors":[repr(e)]})
  finally:browser.close()
 dedup={}
 for r in all_rows:
  if r.get("source_url"):dedup[(r.get("source"),r["source_url"])]=r
 manifest={"source_run_id":run_id,"collector":COLLECTOR_VERSION,"started_at":started,"finished_at":now(),"configured_sources":sum(len(x) for x in SOURCES.values()),"locations":sorted(SOURCES),"records_seen":sum(x.get("records_seen",0) for x in coverage),"records_parsed":sum(x.get("records_parsed",0) for x in coverage),"records_normalized":len(dedup),"records_published":len(dedup),"records_rejected":sum(x.get("records_rejected",0) for x in coverage),"raw_capture_files":sum(x.get("raw_capture",0) for x in coverage),"status_counts":{s:sum(1 for x in coverage if x.get("status")==s) for s in sorted({x.get("status") for x in coverage})},"coverage":coverage}
 (debug/"manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8");(DEBUG/"capture_debug.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
 return list(dedup.values()),coverage
if __name__=="__main__":
 rows,cov=run();print(json.dumps({"collector":COLLECTOR_VERSION,"rows":len(rows),"source_runs":len(cov),"by_status":{s:sum(1 for x in cov if x.get("status")==s) for s in sorted({x.get("status") for x in cov})}},ensure_ascii=False,indent=2))
