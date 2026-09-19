"""Schema-tolerant V3.2 audit for generated radar inventory."""
from __future__ import annotations
import json
from collections import Counter
from pathlib import Path
ROOT=Path(__file__).resolve().parent; DATA=ROOT/"data"; DEBUG=DATA/"debug"
def load(path,default):
    try:return json.loads(path.read_text(encoding="utf-8"))
    except Exception:return default
def rows(payload,key):
    value=payload.get(key,[]) if isinstance(payload,dict) else []
    return list(value.values()) if isinstance(value,dict) else value if isinstance(value,list) else []
def source_runs():
    out=[]
    for location,specs in load(ROOT/"radar"/"sources.json",{}).items():
        if isinstance(specs,list):
            out += [{"location":location,"source":x.get("name","unknown")} for x in specs if isinstance(x,dict) and x.get("url") and x.get("mandatory",True)]
    return out
def main():
    current=load(DATA/"current.json",{}); objects=rows(load(DATA/"objects.json",{}),"objects"); configured=source_runs()
    coverage=current.get("coverage",current.get("capture_coverage",[])) or current.get("coverage_evaluation",{}).get("mandatory_results",[])
    want={(x["location"].lower(),x["source"].lower()) for x in configured}; have={(str(x.get("location","")).lower(),str(x.get("source","")).lower()) for x in coverage}; published=Counter((str(x.get("location","")).lower(),str(x.get("source",x.get("source_id","")).lower())) for x in objects)
    report={"schema_version":"3.2","run_id":current.get("run_id"),"inventory_mode":current.get("inventory_mode",current.get("snapshot_status")),"objects":len(objects),"configured_mandatory_runs":len(want),"reported_mandatory_runs":len(want&have),"missing_run_reports":sorted("%s | %s"%x for x in want-have),"published_by_source_location":{"%s | %s"%x:n for x,n in sorted(published.items())},"provenance_missing":sum(1 for x in objects if not(x.get("raw_reference") or x.get("raw_path") or x.get("source_url"))),"status":"PASS" if want<=have else "DEGRADED"}
    DEBUG.mkdir(parents=True,exist_ok=True);(DEBUG/"inventory_audit.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8");(DEBUG/"inventory_audit.md").write_text("# Inventory audit\n\n"+"\n".join(f"- {k}: {v}" for k,v in report.items() if k not in {"missing_run_reports","published_by_source_location"})+"\n",encoding="utf-8");print(json.dumps(report,ensure_ascii=False,indent=2))
if __name__=="__main__":main()
