"""Deterministically expand the legacy collector registry to V3.2 coverage."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "radar" / "sources.json"
LOCATIONS = ROOT / "data" / "locations.json"

INLAND = [
    ("bologna", "Bologna"), ("zola-predosa", "Zola Predosa"),
    ("casalecchio-di-reno", "Casalecchio di Reno"),
    ("san-lazzaro-di-savena", "San Lazzaro di Savena"),
    ("castenaso", "Castenaso"), ("granarolo-dell-emilia", "Granarolo dell'Emilia"),
    ("ozzano-dell-emilia", "Ozzano dell'Emilia"), ("castel-maggiore", "Castel Maggiore"),
    ("calderara-di-reno", "Calderara di Reno"), ("anzola-dell-emilia", "Anzola dell'Emilia"),
    ("valsamoggia", "Valsamoggia"), ("sasso-marconi", "Sasso Marconi"),
    ("pianoro", "Pianoro"), ("castelfranco-emilia", "Castelfranco Emilia"),
    ("nonantola", "Nonantola"), ("san-cesario-sul-panaro", "San Cesario sul Panaro"),
    ("modena", "Modena"),
]
MICRO = {
 "Bologna":["Savena","San Ruffillo / Via Toscana / Monte Donato","Murri","Santo Stefano / Colli","Costa-Saragozza","San Donato / Fiera","San Donato-San Vitale","Corticella","Borgo Panigale / Casteldebole","Santa Viola","Navile / Lame"],
 "Zola Predosa":["Zola Predosa centro","Riale","Ponte Ronca","Crespellano-facing areas"], "Casalecchio di Reno":["Casalecchio centro","Ceretolo","San Biagio","Bologna / Zola-facing areas"], "San Lazzaro di Savena":["San Lazzaro centro","Idice","Ponticella","La Mura San Carlo","Farneto"], "Castenaso":["Castenaso centro","Villanova","Fiesso","Marano"], "Granarolo dell'Emilia":["Granarolo centro","Quarto Inferiore","Cadriano"], "Ozzano dell'Emilia":["Ozzano centro","Tolara","Ponte Rizzoli","collinare areas"], "Castel Maggiore":["Castel Maggiore centro","Trebbo di Reno","Primo Maggio"], "Calderara di Reno":["Calderara centro","Lippo","Bargellino"], "Anzola dell'Emilia":["Anzola centro","Lavino di Mezzo","Ponte Samoggia"], "Valsamoggia":["Crespellano","Bazzano","Monteveglio"], "Sasso Marconi":["Sasso Marconi centro","Borgonuovo","Pontecchio Marconi","collinare residential areas"], "Pianoro":["Pianoro centro","Rastignano","Carteria di Sesto"], "Castelfranco Emilia":["Castelfranco centro","Panzano","Gaggio di Piano","Manzolino"], "Nonantola":["Nonantola centro","residential expansion zones"], "San Cesario sul Panaro":["San Cesario centro","Panaro corridor"], "Modena":["Buon Pastore","Musicisti","Sant'Agnese","Villaggio Zeta","Vaciglio","Morane","Cognento","Saliceta San Giuliano","Baggiovara","southern / western residential belt"]
}

def spec(name, url):
    return {"name": name, "url": url, "max_pages": 5, "mandatory": True, "scope": "commercial"}

def main():
    data = json.loads(PATH.read_text(encoding="utf-8"))
    for slug, comune in INLAND:
        specs = list(data.pop(slug, data.get(comune, [])))
        existing = {x.get("name") for x in specs}
        candidates = [
            spec("immobiliare_new_build", f"https://www.immobiliare.it/nuove-costruzioni/{slug}/"),
            spec("idealista_new_build", f"https://www.idealista.it/vendita-case/{slug}/con-nuova-costruzione/"),
            spec("casa_new_build", f"https://www.casa.it/vendita/residenziale/in-nuove-costruzioni/{slug}/"),
        ]
        specs.extend(x for x in candidates if x["name"] not in existing)
        data[comune] = specs
    # Explicit first-belt search is a separate required market layer, not a
    # substitute for the Comune records above.
    data["bologna-first-belt"] = [
        spec("idealista_new_build", "https://www.idealista.it/vendita-case/bologna-bologna/con-nuova-costruzione/"),
        spec("casa_new_build", "https://www.casa.it/vendita/residenziale/in-nuove-costruzioni/bologna/"),
    ]
    PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    location_data = json.loads(LOCATIONS.read_text(encoding="utf-8"))
    known = {x.get("comune") for x in location_data.get("locations", [])}
    for slug, comune in INLAND:
        if comune not in known:
            location_data["locations"].append({
                "comune": comune,
                "cluster": "Bologna metro" if comune != "Modena" else "Bologna–Modena corridor",
                "micro_locations": MICRO[comune],
                "inventory_status": "CONFIGURED_NO_VERIFIED_LISTINGS",
                "registry_slug": slug,
            })
    LOCATIONS.write_text(json.dumps(location_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

if __name__ == "__main__":
    main()
