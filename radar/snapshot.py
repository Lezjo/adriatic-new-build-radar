"""Immutable publisher: failed runs retain raw evidence but never replace current."""
from __future__ import annotations
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from radar.collectors.portal_capture import run
from radar.model import build_entities, normalize_rows, source_metrics

ROOT=Path(__file__).resolve().parents[1]; DATA=ROOT/"data"
CURRENT=DATA/"current.json"; OBJECTS=DATA/"objects.json"; LISTINGS=DATA/"listings.json"; PROJECTS=DATA/"projects.json"; UNITS=DATA/"units.json"; HISTORY=DATA/"history"; MANIFESTS=DATA/"manifests"; PRICE_HISTORY=DATA/"price_history.json"; PROMOTIONS=DATA/"promotions.json"; REGISTRY=ROOT/"radar"/"sources.json"
def load(path,default):
    try:return json.loads(path.read_text(encoding="utf-8"))
    except Exception:return default
def write(path,payload):path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8")
def to_day(value):
    try:return datetime.fromisoformat(value.replace("Z","+00:00")).date()
    except (TypeError,ValueError):return None
def pct(current,old):return round((current-old)/old*100,2) if isinstance(current,(int,float)) and isinstance(old,(int,float)) and old else None
def prior_at(points,target):
    rows=[x for x in points if to_day(x.get("observed_at")) and to_day(x["observed_at"])<=target and isinstance(x.get("price"),(int,float))]
    return max(rows,default=None,key=lambda x:x["observed_at"])
def apply_deltas(listings,history,today):
    for item in listings:
        points=sorted(history["listings"].get(item["listing_id"],[]),key=lambda x:x.get("observed_at","")); prior=points[-1] if points else None; price=item.get("price")
        item["previous_price"]=prior.get("price") if prior else None; item["price_delta"]=price-prior["price"] if prior and isinstance(price,(int,float)) and isinstance(prior.get("price"),(int,float)) else None; item["price_delta_pct"]=pct(price,item["previous_price"]); item["price_event"]="NEW" if not prior else "INCREASE" if (item["price_delta"] or 0)>0 else "DECREASE" if (item["price_delta"] or 0)<0 else "UNCHANGED"
        for days in (1,7,30,90,180,365):
            old=prior_at(points,today-timedelta(days=days)); key=f"price_change_{days}d"; item[key]=price-old["price"] if old and isinstance(price,(int,float)) else None; item[key+"_pct"]=pct(price,old["price"]) if old else None; item[key+"_date"]=old.get("observed_at") if old else None
        prices=[x["price"] for x in points if isinstance(x.get("price"),(int,float))]+([price] if isinstance(price,(int,float)) else []); item["first_seen_price"],item["lowest_seen_price"],item["highest_seen_price"]=(prices[0],min(prices),max(prices)) if prices else (None,None,None); item["price_change_all_time"]=price-item["first_seen_price"] if isinstance(price,(int,float)) and isinstance(item["first_seen_price"],(int,float)) else None; item["price_change_all_time_pct"]=pct(price,item["first_seen_price"]); item["current_vs_lowest"]=price-item["lowest_seen_price"] if isinstance(price,(int,float)) and isinstance(item["lowest_seen_price"],(int,float)) else None; item["current_vs_highest"]=price-item["highest_seen_price"] if isinstance(price,(int,float)) and isinstance(item["highest_seen_price"],(int,float)) else None; item["new_to_radar_30d"]=not prior or (today-to_day(points[0]["observed_at"])).days<=30
def update_history(history,listings,observed_at):
    for item in listings:
        if item.get("stale_source"): continue
        if not isinstance(item.get("price"),(int,float)):continue
        points=[x for x in history["listings"].get(item["listing_id"],[]) if x.get("run_id")!=item["run_id"]]; points.append({"observed_at":observed_at,"run_id":item["run_id"],"price":item["price"],"old_price":item.get("old_price"),"new_price":item.get("new_price"),"source_id":item["source_id"],"canonical_url":item["canonical_url"],"title":item["title"]}); history["listings"][item["listing_id"]]=points
    history.update({"schema_version":"3.1","updated_at":observed_at,"key":"listing_id","retention":"all_time"});return history
def publish(rows,manifest,now=None):
    now=now or datetime.now(timezone.utc); registry=load(REGISTRY,{}).get("sources",[]); listings,rejected=normalize_rows(rows,{x["source_id"]:x for x in registry},manifest["run_id"])
    bad={};
    for x in rejected:bad[x["source_id"]]=bad.get(x["source_id"],0)+1
    for capture in manifest["captures"]:
        capture["records_normalized"]=sum(x["source_id"]==capture["source_id"] for x in listings);capture["records_published"]=capture["records_normalized"];capture["records_rejected"]=bad.get(capture["source_id"],0)
        if capture["status"]=="SUCCESS" and not capture["records_normalized"]:capture["status"]="ZERO_VERIFIED"
    write(MANIFESTS/f"{manifest['run_id']}.json",manifest)
    if not listings:
        write(HISTORY/f"{manifest['run_id']}-FAILED_EMPTY.json",{"run_id":manifest["run_id"],"status":"FAILED_EMPTY_CAPTURE","manifest":f"data/manifests/{manifest['run_id']}.json","previous_current_preserved":True});raise RuntimeError("No normalized listings; current inventory preserved.")
    history=load(PRICE_HISTORY,{"schema_version":"3.1","listings":{}});history.setdefault("listings",{});apply_deltas(listings,history,now.date());history=update_history(history,listings,now.isoformat())
    # A blocked source must never make previously published inventory vanish.
    failed={x["source_id"] for x in manifest["captures"] if x.get("status")!="SUCCESS"}
    known={x["listing_id"] for x in listings}
    for old in load(OBJECTS,{"objects":[]}).get("objects",[]):
        if old.get("source_id") in failed and old.get("listing_id") not in known:
            stale=dict(old);stale.update({"stale_source":True,"listing_status":"STALE_SOURCE","stale_since":now.isoformat(),"run_id":manifest["run_id"]});listings.append(stale)
    projects,units,objects=build_entities(listings);promotions=[dict(x["promotion"],listing_id=x["listing_id"],detected_at=now.isoformat()) for x in listings if x.get("promotion")];metrics=source_metrics(registry,listings,manifest);required=[x for x in metrics if x["mandatory"] and x["enabled"]];status="SUCCESS" if required and all(x["status"]=="SUCCESS" and x["accepted"] for x in required) else "DEGRADED"
    totals={"listings":len(listings),"projects":len(projects),"units":len(units),"rejected":len(rejected)};run_payload={"schema_version":"3.1","run_id":manifest["run_id"],"run_date":now.date().isoformat(),"generated_at":now.isoformat(),"snapshot_status":status,"manifest":f"data/manifests/{manifest['run_id']}.json","totals":totals,"listings":listings,"projects":projects,"units":units,"rejected":rejected};write(HISTORY/f"{manifest['run_id']}.json",run_payload);write(LISTINGS,{"schema_version":"3.1","run_id":manifest["run_id"],"listings":listings});write(PROJECTS,{"schema_version":"3.1","run_id":manifest["run_id"],"projects":projects});write(UNITS,{"schema_version":"3.1","run_id":manifest["run_id"],"units":units});write(OBJECTS,{"schema_version":"3.1","run_id":manifest["run_id"],"objects":objects});write(PRICE_HISTORY,history);write(PROMOTIONS,{"schema_version":"3.1","run_id":manifest["run_id"],"promotions":promotions})
    current={"schema_version":"3.1","run_id":manifest["run_id"],"run_date":now.date().isoformat(),"last_live_capture":manifest.get("finished_at"),"snapshot_status":status,"totals":totals,"locations":sorted({x["location"] for x in listings if x.get("location")}),"source_metrics":metrics,"files":{"listings":"data/listings.json","projects":"data/projects.json","units":"data/units.json","objects":"data/objects.json","price_history":"data/price_history.json","promotions":"data/promotions.json","manifest":run_payload["manifest"]}};write(CURRENT,current);return run_payload
def main():
    run_id=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ");rows,manifest=run(run_id);publish(rows,manifest)
if __name__=="__main__":main()
