"""Read-only inventory audit used locally and by GitHub Actions."""
from __future__ import annotations
import json
from collections import Counter, defaultdict
from pathlib import Path
from radar.model import canonical_url

ROOT=Path(__file__).resolve().parent; DATA=ROOT/"data"; OUT=DATA/"debug"/"inventory_audit.json"; MD=DATA/"debug"/"inventory_audit.md"
def load(path,default):
    try:return json.loads(path.read_text(encoding="utf-8"))
    except Exception:return default
def main():
    registry=load(ROOT/"radar"/"sources.json",{}).get("sources",[]); current=load(DATA/"current.json",{}); objects=load(DATA/"objects.json",{}).get("objects",[]); manifest=load(ROOT/current.get("files",{}).get("manifest",""),{}) if current.get("files",{}).get("manifest") else {}
    mandatory=[x for x in registry if x.get("mandatory")]; captures={x.get("source_id"):x for x in manifest.get("captures",[])}; by_source=Counter(x.get("source_id") for x in objects); urls=defaultdict(list)
    for x in objects:
        if x.get("canonical_url"):urls[canonical_url(x["canonical_url"])].append(x.get("source_id"))
    report={"schema_version":"3.1","run_id":current.get("run_id"),"snapshot_status":current.get("snapshot_status"),"objects":len(objects),"mandatory_sources":[{"source_id":x["source_id"],"location":x["location"],"enabled":x.get("enabled"),"capture_status":captures.get(x["source_id"],{}).get("status","NOT_RUN"),"published":by_source[x["source_id"]]} for x in mandatory],"duplicate_canonical_urls_across_sources":[{"canonical_url":u,"source_ids":sorted(set(v))} for u,v in urls.items() if len(set(v))>1],"missing_provenance":[x.get("listing_id") for x in objects if not x.get("source_id") or not x.get("raw_reference")],"missing_urls":[x.get("listing_id") for x in objects if not x.get("canonical_url") or not x.get("observed_urls")]}
    report["pass"] = bool(objects) and not report["missing_provenance"] and not report["missing_urls"]
    OUT.parent.mkdir(parents=True,exist_ok=True);OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    lines=["# Inventory audit",f"- Run: {report['run_id']}",f"- Snapshot: {report['snapshot_status']}",f"- Objects: {report['objects']}",f"- Canonical cross-source candidates: {len(report['duplicate_canonical_urls_across_sources'])}",f"- Missing provenance: {len(report['missing_provenance'])}",f"- Missing URLs: {len(report['missing_urls'])}"]
    MD.write_text("\n".join(lines)+"\n",encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2))
if __name__=="__main__":main()
