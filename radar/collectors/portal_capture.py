# JBC V7 — replacement capture_jbc() for Adriatic New Build Radar
#
# Purpose:
# - Treat JBC as a mandatory independent source layer.
# - Always inspect both homepage and the real JBC construction catalog:
#     https://www.jbcimmobiliare.it/
#     https://www.jbcimmobiliare.it/ls.php?nuove-costruzioni
# - Preserve query strings such as ?nuove-costruzioni in canonical URLs.
# - Do NOT require JBC_TERMS on the detail page.
# - Accept detail pages using property signals OR descriptive slug/title.
# - Keep Project / Unit separation delegated to the existing detail() parser.
# - Write a manifest even when capture returns zero records.

# Replace the existing capture_jbc() function in portal_capture_JBC_V6_ROBUST.py
# (or the current JBC adapter) with the function below.

def capture_jbc(browser, location, spec, debug):
    """JBC V7: mandatory full-inventory crawler.

    Discovery order:
      1. JBC homepage
      2. JBC real construction catalog (?nuove-costruzioni)
      3. internal catalog/property links
      4. detail pages

    Important: JBC uses /ls.php with a query-string route. The bare
    /ls.php is NOT a valid catalog page and must never replace the
    ?nuove-costruzioni URL.
    """
    source = spec["name"]
    max_hubs = int(spec.get("max_hub_pages", 25))
    max_details = int(spec.get("max_detail_pages", 400))

    JBC_HOME = "https://www.jbcimmobiliare.it/"
    JBC_NEW_BUILDS = "https://www.jbcimmobiliare.it/ls.php?nuove-costruzioni"

    # Keep an explicitly configured start URL, but always add the two
    # mandatory JBC discovery entry points.
    starts = [
        canonical(spec.get("url") or JBC_HOME),
        canonical(JBC_HOME),
        canonical(JBC_NEW_BUILDS),
    ]
    starts = list(dict.fromkeys(starts))

    ctx = browser.new_context(
        locale="it-IT",
        user_agent=(
            "Mozilla/5.0 (X11; Linux x86_64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0 Safari/537.36"
        ),
    )
    ctx.set_default_timeout(10000)

    page = ctx.new_page()
    page.set_default_navigation_timeout(30000)

    hub_queue = []
    queued_hubs = set()
    visited_hubs = set()
    detail_urls = {}
    errors = []
    pages = 0
    last_status = None
    last_title = None

    def internal(u):
        try:
            p = urlparse(canonical(u))
            return p.netloc.lower().removeprefix("www.") == JBC_HOST
        except Exception:
            return False

    def bad(u):
        path = urlparse(canonical(u)).path.lower()
        return any(x in path for x in BAD_PATH_HINTS)

    def is_bare_ls(u):
        p = urlparse(canonical(u))
        return p.path.lower().rstrip("/") == "/ls.php" and not p.query

    def add_hub(u):
        u = canonical(u)
        if (
            internal(u)
            and not bad(u)
            and not is_bare_ls(u)
            and u not in queued_hubs
            and u not in visited_hubs
            and len(queued_hubs) < max_hubs
        ):
            queued_hubs.add(u)
            hub_queue.append(u)

    def add_detail(u, label=""):
        u = canonical(u)
        if (
            internal(u)
            and not bad(u)
            and not is_bare_ls(u)
            and u.rstrip("/") not in {
                x.rstrip("/") for x in starts if "ls.php" not in x
            }
            and len(detail_urls) < max_details
        ):
            detail_urls.setdefault(u, label)

    for s in starts:
        if is_bare_ls(s):
            continue
        add_hub(s)

    def extract_links(base):
        """Extract href/data-href/data-url/onclick destinations."""
        try:
            rows = page.locator(
                "a[href], [data-href], [data-url]"
            ).evaluate_all(
                """els => els.map(el => ({
                    href: el.getAttribute('href') || '',
                    dataHref: el.getAttribute('data-href') || '',
                    dataUrl: el.getAttribute('data-url') || '',
                    onclick: el.getAttribute('onclick') || '',
                    text: (el.innerText || el.getAttribute('aria-label') || '').trim()
                }))"""
            )
        except Exception as exc:
            errors.append(f"extract_links: {type(exc).__name__}: {exc!r}")
            return

        for row in rows:
            text = " ".join((row.get("text") or "").split())
            low = text.lower()

            candidates = [
                row.get("href"),
                row.get("dataHref"),
                row.get("dataUrl"),
            ]

            onclick = row.get("onclick") or ""
            candidates += re.findall(
                r"""(?:location(?:\.href)?|window\.open)\s*\(?['"]?([^'")]+)""",
                onclick,
                flags=re.I,
            )

            for raw in candidates:
                if not raw:
                    continue

                raw = str(raw).strip()
                if raw.startswith(("javascript:", "mailto:", "tel:")):
                    continue

                u = canonical(urljoin(base, raw))
                if not internal(u) or bad(u):
                    continue

                p = urlparse(u)
                path = p.path.lower()
                query = p.query.lower()
                hay = f"{low} {path} {query}"

                # Never crawl the invalid bare /ls.php endpoint.
                if is_bare_ls(u):
                    continue

                # Real JBC catalog routes are hubs.
                if (
                    "nuove-costruzioni" in query
                    or "nuove_costruzioni" in query
                    or "vedi tutti i cantieri" in low
                    or "vedi tutti cantieri" in low
                    or "i nostri cantieri" in low
                    or "nostri cantieri" in low
                    or "/cantieri" in path
                    or "/cantiere" in path
                ):
                    add_hub(u)
                    continue

                # Explicit detail CTA.
                if (
                    "vedi immobile" in low
                    or "scopri immobile" in low
                    or "mostra immobile" in low
                    or "dettagli" in low
                ):
                    add_detail(u, text)
                    continue

                # JBC detail URLs are often descriptive slugs without a
                # standard /annunci/ route. A descriptive internal slug is
                # therefore enough to inspect.
                slug = path.strip("/")
                descriptive_slug = (
                    len(slug) >= 18
                    and slug.count("-") >= 2
                    and not any(x in path for x in (
                        "/privacy", "/cookie", "/contatti", "/contact",
                        "/franchising", "/newsletter", "/login",
                        "/registr", "/agenzia", "/agenzie",
                    ))
                )

                if descriptive_slug:
                    add_detail(u, text)
                    continue

                # Property vocabulary in the card/anchor is a useful
                # discovery signal, but NOT a mandatory detail-page filter.
                if any(term in hay for term in JBC_TERMS):
                    add_detail(u, text)

    def click_visible_ctas():
        """Fallback for cards whose destination is generated by JS."""
        for selector in (
            'text="Vedi Immobile"',
            'text="Vedi immobile"',
            'text="Vedi Tutti i Cantieri"',
        ):
            try:
                loc = page.locator(selector)
                n = min(loc.count(), max(0, max_details - len(detail_urls)))
                for i in range(n):
                    try:
                        before = canonical(page.url)
                        loc.nth(i).click(timeout=2500)
                        page.wait_for_timeout(300)
                        current = canonical(page.url)

                        if (
                            internal(current)
                            and not is_bare_ls(current)
                            and current != before
                        ):
                            add_detail(current, "JS CTA")

                        page.go_back(
                            wait_until="domcontentloaded",
                            timeout=10000,
                        )
                    except Exception:
                        pass
            except Exception:
                pass

    def inspect_hub(u):
        nonlocal pages, last_status, last_title

        try:
            response = page.goto(
                u,
                wait_until="domcontentloaded",
                timeout=30000,
            )

            last_status = response.status if response else None
            last_title = page.title() or ""
            pages += 1

            page.wait_for_timeout(900)

            # Lazy-load the catalog cards.
            for _ in range(5):
                try:
                    page.mouse.wheel(0, 1800)
                    page.wait_for_timeout(350)
                except Exception:
                    break

            extract_links(u)
            click_visible_ctas()

        except PlaywrightTimeoutError:
            errors.append(f"hub {u}: TIMEOUT")
        except Exception as exc:
            errors.append(f"hub {u}: {type(exc).__name__}: {exc!r}")

    while hub_queue and len(visited_hubs) < max_hubs:
        u = hub_queue.pop(0)
        if u in visited_hubs:
            continue

        visited_hubs.add(u)
        inspect_hub(u)

    records = []
    visited_details = []

    for u, fallback in list(detail_urls.items())[:max_details]:
        visited_details.append(u)

        try:
            response = page.goto(
                u,
                wait_until="domcontentloaded",
                timeout=30000,
            )

            if response and response.status >= 400:
                errors.append(f"detail {u}: HTTP {response.status}")
                continue

            page.wait_for_timeout(500)

            try:
                title = page.title() or ""
            except Exception:
                title = ""

            try:
                body = " ".join(
                    (page.locator("body").inner_text(timeout=5000) or "").split()
                )
            except Exception:
                body = fallback or ""

            if len(body) < 50:
                continue

            low = f"{title} {body}".lower()
            path = urlparse(u).path.lower()

            # Skip obvious non-property pages.
            if any(x in low for x in (
                "privacy policy",
                "cookie policy",
                "lavora con noi",
                "franchising",
            )):
                continue

            # V7 acceptance rule:
            # JBC_TERMS are a positive signal, NOT a gate.
            #
            # A detail page is accepted when:
            #   A) it contains property/project signals, OR
            #   B) it has a descriptive JBC slug, OR
            #   C) the title itself is descriptive.
            has_property_signal = any(term in low for term in JBC_TERMS)
            has_descriptive_slug = (
                path.count("-") >= 2
                and len(path.strip("/")) >= 18
            )
            has_descriptive_title = (
                len(re.sub(r"\s+", " ", title).strip()) >= 20
                and "jbc" in low
            )

            if not (
                has_property_signal
                or has_descriptive_slug
                or has_descriptive_title
            ):
                continue

            # IMPORTANT:
            # Do not apply another JBC_TERMS filter after this point.
            # Existing detail() is responsible for extracting price,
            # area, rooms, energy, features, unit/project type, etc.
            records.append(
                detail(
                    page,
                    source,
                    location,
                    u,
                    fallback,
                )
            )

        except PlaywrightTimeoutError:
            errors.append(f"detail {u}: TIMEOUT")
        except Exception as exc:
            errors.append(
                f"detail {u}: {type(exc).__name__}: {exc!r}"
            )

    ded = {
        r["source_url"]: r
        for r in records
        if r.get("source_url")
    }

    manifest = {
        "version": "JBC-V7-MANDATORY-FULL",
        "source": source,
        "location": location,
        "start_urls": starts,
        "mandatory_new_build_url": JBC_NEW_BUILDS,
        "hub_pages_visited": len(visited_hubs),
        "hub_pages": sorted(visited_hubs),
        "candidate_detail_urls": len(detail_urls),
        "detail_pages_visited": len(visited_details),
        "records_captured": len(ded),
        "unit_records": sum(
            1 for r in ded.values()
            if r.get("record_type") == "UNIT"
        ),
        "project_records": sum(
            1 for r in ded.values()
            if r.get("record_type") == "PROJECT"
        ),
        "projects_detected": len({
            r.get("project_id")
            for r in ded.values()
            if r.get("project_id")
        }),
        "last_http_status": last_status,
        "last_title": last_title,
        "errors": errors[:150],
        "candidate_urls": list(detail_urls.keys()),
    }

    try:
        debug.mkdir(parents=True, exist_ok=True)
        (debug / f"{location}__jbc_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        errors.append(
            f"manifest-write: {type(exc).__name__}: {exc!r}"
        )

    ctx.close()

    return (
        list(ded.values()),
        pages,
        last_status,
        last_title,
        None if ded else "JBC V7 adapter found 0 detail records",
    )
