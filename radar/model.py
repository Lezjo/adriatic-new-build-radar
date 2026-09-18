"""Canonical, loss-aware Project → Unit → Listing model."""
from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

TRACKING_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term", "fbclid", "gclid"}
LOCATION_RULES = {
    "Ca' Gamba": ("ca' gamba", "ca gamba", "ca fuser", "ca stocco", "ca martin", "borgo ina"),
    "Jesolo Paese": ("jesolo paese", "via roma", "piave vecchia"),
    "Jesolo": ("jesolo", "lido di jesolo", "cortellazzo", "piazza mazzini", "piazza milano"),
    "Caorle": ("caorle", "porto santa margherita", "altanea", "duna verde", "brussa"),
    "Cavallino-Treporti": ("cavallino", "treporti", "ca' savio", "ca savio", "punta sabbioni", "ca vio"),
    "San Donà di Piave": ("san donà", "san dona", "mussetta", "calvecchia"),
    "Treviso": ("treviso", "silea", "casier", "carbonera", "villorba", "mogliano"),
}
PROMOTION_TERMS = ("sconto", "ribassato", "ribasso", "prezzo ribassato", "offerta", "promo", "promozione", "prezzo speciale", "ultimo prezzo", "vendita urgente", "incluso nel prezzo")

def now_iso(): return datetime.now(timezone.utc).isoformat()

def canonical_url(value):
    if not value: return None
    p = urlparse(str(value).strip())
    if p.scheme not in {"http", "https"} or not p.netloc: return None
    q = [(k,v) for k,v in parse_qsl(p.query, keep_blank_values=True) if k.lower() not in TRACKING_PARAMS]
    return urlunparse((p.scheme.lower(),p.netloc.lower(),p.path.rstrip("/"),"",urlencode(sorted(q)),""))

def normalise_text(value):
    return re.sub(r"\s+", " ", re.sub(r"[^\w]+", " ", str(value or "").lower(), flags=re.UNICODE)).strip()

def classify_location(*, title=None, url=None, address=None, body=None, fallback=None):
    scores, evidence = Counter(), []
    for field, value, weight in (("url",url,8),("title",title,6),("address",address,5),("body",body,1)):
        text = normalise_text(value)
        for location, terms in LOCATION_RULES.items():
            hits = [term for term in terms if normalise_text(term) in text]
            if hits: scores[location] += weight*len(hits); evidence.append({"field":field,"location":location,"terms":hits,"score":weight*len(hits)})
    if not scores: return fallback, "configured_fallback" if fallback else "unknown", evidence
    winners = sorted(k for k,v in scores.items() if v == max(scores.values()))
    if len(winners) != 1: return fallback, "ambiguous" if fallback else "unknown", evidence
    return winners[0], "high" if any(x["score"] >= 6 and x["location"] == winners[0] for x in evidence) else "medium", evidence

def stable_id(prefix, *parts):
    return prefix + ":" + hashlib.sha1("|".join(normalise_text(x) for x in parts).encode()).hexdigest()
def listing_id(source_id, url): return stable_id("listing", source_id, url)
def project_id(location, title, address): return stable_id("project", location, title, address)
def unit_id(project, raw_unit, listing): return stable_id("unit", project, raw_unit or listing)

def promotion_from(raw, text):
    old, new = raw.get("old_price"), raw.get("new_price") or raw.get("price")
    terms = [x for x in PROMOTION_TERMS if x in normalise_text(text)]
    if old is None and not terms: return None
    amount = old-new if isinstance(old,(int,float)) and isinstance(new,(int,float)) else None
    return {"promotion_id":stable_id("promotion",raw.get("source_id"),raw.get("source_url"),old,new),"old_price":old,"new_price":new,"discount_amount":amount,"discount_percent":round(amount/old*100,2) if amount is not None and old else None,"promotion_text":text[:500] if terms else None,"promotion_type":"explicit_old_new" if old is not None else "keyword_signal","source_id":raw.get("source_id"),"evidence":terms}

def normalize_rows(rows, source_registry, run_id):
    """Deduplicate exact URLs per source only; return rejected observations separately."""
    accepted, rejected = {}, []
    for index, raw in enumerate(rows):
        source_id = raw.get("source_id") or raw.get("source") or "unknown"; config = source_registry.get(source_id,{})
        observed = raw.get("source_url") or raw.get("url"); url = canonical_url(observed)
        if not url:
            rejected.append({"source_id":source_id,"raw_reference":raw.get("raw_reference"),"row_index":index,"reason":"missing_or_invalid_source_url"}); continue
        title = raw.get("title") or raw.get("listing_title") or "Untitled listing"
        location, confidence, evidence = classify_location(title=title,url=url,address=raw.get("address"),body=raw.get("raw_text"),fallback=config.get("location") if config.get("location") != "Adriatic coverage" else None)
        key, prior = (source_id,url), accepted.get((source_id,url),{})
        refs = prior.get("raw_references",[]) + ([raw["raw_reference"]] if raw.get("raw_reference") else [])
        urls = set(prior.get("observed_urls",[])) | {url,observed}
        pid = raw.get("project_id") or project_id(location,raw.get("project") or title,raw.get("address")); lid = raw.get("listing_id") or listing_id(source_id,url); raw_unit = raw.get("unit_id") or raw.get("unit")
        text = " ".join(str(raw.get(k) or "") for k in ("raw_text","listing_title","title"))
        accepted[key] = {"listing_id":lid,"project_id":pid,"unit_id":unit_id(pid,raw_unit,lid) if raw_unit else None,"source_id":source_id,"source_name":raw.get("source_name") or config.get("source_name") or source_id,"source_url":observed,"canonical_url":url,"observed_urls":sorted(x for x in urls if x),"raw_reference":raw.get("raw_reference"),"raw_references":sorted(set(refs)),"run_id":run_id,"captured_at":raw.get("captured_at") or now_iso(),"first_seen":raw.get("first_seen"),"last_seen":raw.get("last_seen"),"location":location,"location_confidence":confidence,"location_evidence":evidence,"title":title,"address":raw.get("address"),"listing_status":raw.get("listing_status") or raw.get("status") or "ACTIVE","price":raw.get("price"),"old_price":raw.get("old_price"),"new_price":raw.get("new_price"),"surface_m2":raw.get("surface_m2",raw.get("area_m2")),"rooms":raw.get("rooms"),"bedrooms":raw.get("bedrooms"),"floor":raw.get("floor"),"energy_class":raw.get("energy_class"),"features":raw.get("features") or [],"promotion":promotion_from(raw,text)}
    return list(accepted.values()), rejected

def build_entities(listings):
    projects, units = {}, {}
    for item in listings:
        p = projects.setdefault(item["project_id"],{"project_id":item["project_id"],"project_name":item["title"],"location":item["location"],"listing_ids":[],"unit_ids":[],"source_ids":[],"observed_urls":[]})
        p["listing_ids"].append(item["listing_id"]); p["source_ids"].append(item["source_id"]); p["observed_urls"].extend(item["observed_urls"])
        if item.get("unit_id"):
            u = units.setdefault(item["unit_id"],{"unit_id":item["unit_id"],"project_id":item["project_id"],"listing_ids":[],"observed_urls":[]}); u["listing_ids"].append(item["listing_id"]); u["observed_urls"].extend(item["observed_urls"]); p["unit_ids"].append(item["unit_id"])
    for collection in (projects.values(),units.values()):
        for item in collection:
            for key in ("listing_ids","unit_ids","source_ids","observed_urls"):
                if key in item: item[key] = sorted(set(item[key]))
    objects=[]
    for item in listings:
        obj=dict(item); price,area=obj.get("price"),obj.get("surface_m2"); obj.update({"object_id":stable_id("object",item["listing_id"]),"listing_title":item["title"],"area_m2":area,"source":item["source_name"],"eur_m2":round(price/area) if isinstance(price,(int,float)) and isinstance(area,(int,float)) and area else None}); objects.append(obj)
    return sorted(projects.values(),key=lambda x:x["project_id"]),sorted(units.values(),key=lambda x:x["unit_id"]),objects

def source_metrics(registry,listings,manifest):
    captures={x["source_id"]:x for x in manifest.get("captures",[])}; grouped=defaultdict(list)
    for x in listings:
        if not x.get("stale_source"): grouped[x["source_id"]].append(x)
    return [{"source_id":x["source_id"],"source_name":x["source_name"],"location":x["location"],"mandatory":x.get("mandatory",False),"enabled":x.get("enabled",False),"discovered":captures.get(x["source_id"],{}).get("records_seen",0),"accepted":len(grouped[x["source_id"]]),"rejected":captures.get(x["source_id"],{}).get("records_rejected",0),"status":captures.get(x["source_id"],{}).get("status",x.get("status","NOT_RUN")),"last_capture":captures.get(x["source_id"],{}).get("finished_at")} for x in registry]
