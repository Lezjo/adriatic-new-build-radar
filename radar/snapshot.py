from __future__ import annotations
import json, hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collectors.portal_capture import run

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/"data"
CURRENT=DATA/"current.json"
OBJECTS=DATA/"objects.json"
HISTORY=DATA/"history"

def load(p, default):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default

def stable_project_id(location,title):
    key=f"{location}|{title.lower().strip()}"
    return "project:"+hashlib.sha1(key.encode()).hexdigest()

def normalize(rows, run_date):
    out=[]
    for r in rows:
        title=r.get("listing_title") or "Untitled listing"
        location=r.get("location")
        out.append({
            "object_id":"obj:"+hashlib.sha1((r["listing_id"]).encode()).hexdigest(),
            "location":location,
            "project_id":stable_project_id(location,title),
            "unit_id":None,
            "listing_id":r["listing_id"],
            "project":None,
            "unit":None,
            "listing_title":title,
            "price":r.get("price"),
            "area_m2":r.get("area_m2"),
            "eur_m2": (round(r["price"]/r["area_m2"]) if r.get("price") and r.get("area_m2") else None),
            "bedrooms":None,"rooms":None,"floor":None,"parking":None,"garage":None,
            "terrace":None,"pool":None,"sea_distance_m":None,"delivery":None,
            "stage":None,"status":"active_snapshot",
            "source":r.get("source"),"source_url":r.get("source_url"),
            "first_seen":run_date,"last_seen":run_date,
            "previous_price":None,"price_delta":None,"new_to_radar_30d":True
        })
    # Cross-source dedup: canonical source URL is the strongest available key.
    dedup={}
    for o in out:
        dedup[o["source_url"]]=o
    return list(dedup.values())

def main():
    run_date=datetime.now(timezone.utc).date().isoformat()
    rows,coverage=run()
    objects=normalize(rows,run_date)

    prev=load(OBJECTS,{"objects":[]}).get("objects",[])
    prev_by_url={o.get("source_url"):o for o in prev if o.get("source_url")}

    for o in objects:
        p=prev_by_url.get(o["source_url"])
        if p:
            o["first_seen"]=p.get("first_seen",run_date)
            o["previous_price"]=p.get("price")
            if p.get("price") is not None and o.get("price") is not None:
                o["price_delta"]=o["price"]-p["price"]
            try:
                first=datetime.fromisoformat(o["first_seen"]).date()
                o["new_to_radar_30d"]=(datetime.fromisoformat(run_date).date()-first).days<=30
            except Exception: pass

    DATA.mkdir(exist_ok=True); HISTORY.mkdir(exist_ok=True)
    obj_payload={
        "schema_version":"2.1",
        "run_date":run_date,
        "objects":objects,
        "counts":{"total_captured_listings":len(objects),
                  "by_location":{loc:sum(1 for o in objects if o["location"]==loc)
                                 for loc in sorted({o["location"] for o in objects})}},
        "coverage":coverage
    }
    OBJECTS.write_text(json.dumps(obj_payload,ensure_ascii=False,indent=2),encoding="utf-8")

    current=load(CURRENT,{})
    current["run_date"]=run_date
    current["last_live_capture"]=datetime.now(timezone.utc).isoformat()
    current["objects_file"]="data/objects.json"
    current["capture_coverage"]=coverage
    current["inventory_counts"]={
        loc:{
            "captured_live":sum(1 for o in objects if o["location"]==loc),
            "portal_inventory":current.get("inventory_counts",{}).get(loc,{}).get("portal_inventory")
        } for loc in sorted({o["location"] for o in objects})
    }
    CURRENT.write_text(json.dumps(current,ensure_ascii=False,indent=2),encoding="utf-8")

    (HISTORY/f"{run_date}.json").write_text(json.dumps({
        "run_date":run_date,"capture_coverage":coverage,
        "counts":obj_payload["counts"]
    },ensure_ascii=False,indent=2),encoding="utf-8")

if __name__=="__main__":
    main()
