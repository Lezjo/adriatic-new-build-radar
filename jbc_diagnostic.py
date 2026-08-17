from __future__ import annotations

import json
import re
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

from playwright.sync_api import sync_playwright

URL = "https://www.jbcimmobiliare.it/"
OUT = Path("data/debug/jbc_diagnostic")
OUT.mkdir(parents=True, exist_ok=True)


def safe_name(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9._-]+", "_", value)
    return value[:100] or "page"


def main():
    report = {
        "start_url": URL,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "pages": [],
        "errors": [],
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            locale="it-IT",
            viewport={"width": 1440, "height": 1000},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0 Safari/537.36"
            ),
        )
        page = context.new_page()

        def capture(label: str, url: str):
            item = {
                "label": label,
                "requested_url": url,
                "url": "",
                "title": "",
                "http_status": None,
                "body_length": 0,
                "links": [],
                "data_hrefs": [],
                "data_urls": [],
                "onclicks": [],
                "buttons": [],
                "text_excerpt": "",
                "error": None,
            }

            try:
                response = page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=30000,
                )

                item["url"] = page.url
                item["http_status"] = response.status if response else None

                # Give JS a short, controlled amount of time.
                page.wait_for_timeout(2500)

                item["title"] = page.title()
                body = page.locator("body").inner_text(timeout=5000) or ""
                item["body_length"] = len(body)
                item["text_excerpt"] = " ".join(body.split())[:5000]

                links = []
                for a in page.locator("a").all():
                    try:
                        href = a.get_attribute("href")
                        txt = " ".join((a.inner_text() or "").split())
                        if href:
                            links.append({
                                "text": txt[:300],
                                "href": href,
                                "absolute": urljoin(page.url, href),
                            })
                    except Exception:
                        pass

                item["links"] = links[:1000]

                for selector, key, limit in [
                    ("[data-href]", "data-href", 500),
                    ("[data-url]", "data-url", 500),
                    ("[onclick]", "onclick", 500),
                ]:
                    vals = []
                    for el in page.locator(selector).all():
                        try:
                            v = el.get_attribute(key)
                            if v:
                                vals.append(v[:1000])
                        except Exception:
                            pass
                    item[
                        "data_hrefs" if key == "data-href"
                        else "data_urls" if key == "data-url"
                        else "onclicks"
                    ] = vals[:limit]

                btns = []
                for el in page.locator("button").all():
                    try:
                        btns.append(" ".join((el.inner_text() or "").split())[:300])
                    except Exception:
                        pass
                item["buttons"] = btns[:300]

                prefix = safe_name(label)

                (OUT / f"{prefix}.html").write_text(
                    page.content(),
                    encoding="utf-8",
                )

                page.screenshot(
                    path=str(OUT / f"{prefix}.png"),
                    full_page=True,
                )

            except Exception as exc:
                item["error"] = f"{type(exc).__name__}: {exc!r}"
                report["errors"].append(item["error"])

            report["pages"].append(item)

        # 1. Homepage exactly as Actions sees it.
        capture("01_home", URL)

        # 2. Extract promising internal links from the actual DOM.
        try:
            hrefs = []
            for a in page.locator("a[href]").all():
                href = a.get_attribute("href")
                text = " ".join((a.inner_text() or "").split()).lower()
                if not href:
                    continue

                absolute = urljoin(page.url, href)
                host = urlparse(absolute).netloc.lower().removeprefix("www.")
                path = urlparse(absolute).path.lower()

                if host != "jbcimmobiliare.it":
                    continue

                hay = f"{text} {path}"
                if any(term in hay for term in (
                    "cantiere",
                    "cantieri",
                    "immobile",
                    "immobili",
                    "nuov",
                    "appart",
                    "casa",
                    "vendita",
                    "residenz",
                    "jesolo",
                    "caorle",
                )):
                    hrefs.append((text[:200], absolute))

            seen = set()
            candidates = []
            for text, href in hrefs:
                if href not in seen:
                    seen.add(href)
                    candidates.append((text, href))

            report["candidate_links"] = candidates[:100]

            # Capture at most 8 candidates, so diagnostics cannot hang.
            for i, (text, href) in enumerate(candidates[:8], start=1):
                capture(f"02_candidate_{i}_{text or 'link'}", href)

        except Exception as exc:
            report["errors"].append(
                f"candidate discovery: {type(exc).__name__}: {exc!r}"
            )

        browser.close()

    report["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")

    (OUT / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("===== JBC DIAGNOSTIC =====")
    print(json.dumps(report, ensure_ascii=False, indent=2)[:30000])
    print("===== FILES =====")
    for f in sorted(OUT.rglob("*")):
        if f.is_file():
            print(f"{f} {f.stat().st_size} bytes")


if __name__ == "__main__":
    main()
