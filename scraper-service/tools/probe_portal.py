"""Portal reconnaissance — identify a tender portal's architecture.

Run this from a machine that can reach the portal. It answers, by
observation, the questions an agent author cannot answer by reasoning, and
prints values ready to paste into that portal's contract block.

The three Israeli portals in scope are three different architectures, and
which one you are facing decides the whole agent design:

    gov.il/he/collectors/publications          server-rendered or XHR
    online.mod.gov.il/.../BalamList.aspx       ASP.NET WebForms (postback paging)
    sapakim.mod.gov.il/home                    SPA (JSON API behind it)

Usage — the script is self-contained and runs from any directory. It needs
only Playwright, not the service's full requirements.txt:

    pip install playwright
    playwright install chromium

    python tools/probe_portal.py --url "https://sapakim.mod.gov.il/home"
    python tools/probe_portal.py --url "<BALAM url>" --detail-sample
    python tools/probe_portal.py --url "<gov.il url>" --out report.json

On Windows PowerShell use backslashes and run one command per line —
PowerShell 5.1 does not accept `&&` as a separator:

    python tools\probe_portal.py --url "https://sapakim.mod.gov.il/home"

What it reports:

  1. robots.txt — is the path allowed, is a crawl-delay set. Stops if not.
  2. Architecture — WebForms vs SPA vs server-rendered HTML.
  3. JSON endpoints observed over XHR. **If any appear, build against them
     rather than CSS selectors** — an API survives redesigns that break every
     selector, and the response is already structured.
  4. Which candidate selectors match, and how many elements each found.
  5. For WebForms: the GridView's column order, which is what the positional
     `col_*` indexes in the contract must match.
  6. Saved HTML fixtures, so parsing tests run against real markup.

Read-only: it loads pages a browser would load anyway, one at a time, and
writes nothing to the portal.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

FIXTURE_ROOT = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
USER_AGENT = "TendersFinderBot/0.1 (+https://tendersfinder.example/bot)"

ITEM_CANDIDATES = [
    "table[id*='grd'] tr",
    "table[id*='gv'] tr",
    "table.GridView tr",
    "[data-testid='publication-card']",
    ".publication-item",
    "li.result-item",
    "article",
    ".card",
    "[class*='publication']",
    "[class*='result']",
    "[class*='tender']",
    "[class*='michraz']",
]

TITLE_CANDIDATES = ["h2", "h3", ".publication-title", "[class*='title']", "td", "a"]
PAGER_CANDIDATES = [
    ".GridPager",
    "tr.pager",
    "[id*='Pager']",
    "a[rel='next']",
    ".pagination",
    "[class*='pagin']",
    "[aria-label*='הבא']",
]


def check_robots(url: str) -> dict:
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"

    parser = RobotFileParser()
    parser.set_url(robots_url)
    try:
        parser.read()
    except Exception as exc:  # noqa: BLE001
        # A missing robots.txt is not permission; it is an unknown. Report it
        # and let a human decide.
        return {"ok": False, "url": robots_url, "error": f"{type(exc).__name__}: {exc}"}

    return {
        "ok": True,
        "url": robots_url,
        "allowed_for_our_agent": parser.can_fetch(USER_AGENT, url),
        "allowed_for_wildcard": parser.can_fetch("*", url),
        "crawl_delay": parser.crawl_delay(USER_AGENT) or parser.crawl_delay("*"),
    }


def detect_architecture(html: str, json_calls: list[dict]) -> dict:
    """Classify the page. This decides the agent's whole navigation model."""
    webforms_markers = [m for m in ("__VIEWSTATE", "__doPostBack", "__EVENTVALIDATION") if m in html]

    if webforms_markers:
        return {
            "kind": "aspnet_webforms",
            "markers": webforms_markers,
            "implication": (
                "Pagination is a postback: the URL never changes, pages cannot be requested "
                "directly or fetched in parallel, and __VIEWSTATE must round-trip. Drive a "
                "real browser and walk pages in order. See app/agents/mod_balam.py."
            ),
        }

    if json_calls:
        return {
            "kind": "spa_with_json_api",
            "implication": (
                "Build against the JSON endpoints below, not CSS selectors. An API survives "
                "redesigns that break every selector, returns structured data, and is far "
                "cheaper to poll. This is the best of the three architectures to target."
            ),
        }

    return {
        "kind": "server_rendered_html",
        "implication": (
            "Standard listing/detail scraping with CSS selectors. See "
            "app/agents/playwright_agent.py."
        ),
    }


async def probe(url: str, *, detail_sample: bool, headless: bool, wait_ms: int) -> dict:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        # The probe only needs Playwright — not the service's full
        # requirements.txt — so say exactly that rather than sending someone
        # to install anthropic and supabase to look at a web page.
        raise SystemExit(
            "Playwright is not installed. This probe needs only Playwright:\n"
            "    pip install playwright\n"
            "    playwright install chromium"
        ) from None

    report: dict = {"url": url}
    parsed = urlparse(url)
    fixture_dir = FIXTURE_ROOT / parsed.netloc.replace(".", "_")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=headless)
        context = await browser.new_context(user_agent=USER_AGENT, locale="he-IL")
        page = await context.new_page()

        json_calls: list[dict] = []

        async def on_response(response):
            content_type = (response.headers or {}).get("content-type", "")
            if "json" not in content_type.lower():
                return
            if response.request.resource_type not in ("xhr", "fetch"):
                return
            entry = {
                "method": response.request.method,
                "url": response.url,
                "status": response.status,
                "post_data": response.request.post_data,
            }
            try:
                body = await response.json()
                entry["response_shape"] = describe_shape(body)
            except Exception:  # noqa: BLE001
                entry["response_shape"] = "(unreadable)"
            json_calls.append(entry)

        page.on("response", on_response)

        response = await page.goto(url, wait_until="networkidle", timeout=90_000)
        report["http_status"] = response.status if response else None
        report["final_url"] = page.url
        report["title"] = await page.title()

        # SPAs often fetch after networkidle; give late XHR a chance to land.
        await page.wait_for_timeout(wait_ms)

        html = await page.content()
        report["html_bytes"] = len(html)
        report["json_endpoints"] = json_calls
        report["architecture"] = detect_architecture(html, json_calls)

        if report["http_status"] and report["http_status"] >= 400:
            report["warning"] = (
                f"HTTP {report['http_status']}. If this portal is geo-restricted, re-run "
                "from an Israeli egress."
            )

        matches: dict[str, object] = {}
        for selector in ITEM_CANDIDATES:
            try:
                matches[selector] = len(await page.query_selector_all(selector))
            except Exception as exc:  # noqa: BLE001
                matches[selector] = f"error: {exc}"
        report["item_selector_matches"] = matches

        best = max(
            (s for s, n in matches.items() if isinstance(n, int) and n > 1),
            key=lambda s: matches[s],
            default=None,
        )
        report["suggested_item_selector"] = best

        if best:
            elements = await page.query_selector_all(best)
            sample = elements[1] if len(elements) > 1 else elements[0]
            report["sample_item_text"] = (await sample.inner_text())[:1200]
            report["sample_item_html"] = (await sample.inner_html())[:4000]

            # Positional column map — this is what the contract's col_*
            # indexes have to line up with.
            cells = await sample.query_selector_all("td")
            if cells:
                report["column_map"] = {
                    str(i): (await cell.inner_text()).strip()[:80]
                    for i, cell in enumerate(cells)
                }

            link = await sample.query_selector("a[href]")
            href = await link.get_attribute("href") if link else None
            report["sample_item_href"] = href

            # Every link in the sample row, verbatim and unfiltered. The
            # pattern-matched dump above can only report calls it recognizes;
            # this reports what is actually there, which is what you need when
            # the recognizer is the thing that is wrong.
            report["sample_row_links"] = await sample.evaluate(
                """(node) => Array.from(node.querySelectorAll('a, [onclick]')).slice(0, 10).map(a => ({
                    text: (a.innerText || '').trim().slice(0, 60),
                    id: a.id || null,
                    href: a.getAttribute('href'),
                    onclick: a.getAttribute('onclick'),
                }))"""
            )

            if href and href.strip().lower().startswith("javascript:"):
                report["href_warning"] = (
                    "Row links are javascript postbacks, not URLs. Each tender needs a real "
                    "source URL to stay auditable. `sample_row_links` below shows them "
                    "verbatim; with --detail-sample the probe also clicks one and reports "
                    "where it lands, which tells you whether a real detail URL exists at all."
                )

        pagers = {}
        for selector in PAGER_CANDIDATES:
            pagers[selector] = len(await page.query_selector_all(selector))
        report["pager_candidates"] = pagers

        # On a WebForms page, both the row links and the pager are usually
        # __doPostBack calls rather than hrefs, so no CSS guess finds them.
        # Dumping the actual postback targets answers two questions at once:
        # which control opens a tender, and which control changes the page.
        report["postback_targets"] = await collect_postback_targets(page)
        if not any(isinstance(n, int) and n > 0 for n in pagers.values()):
            report["pager_note"] = (
                "No pager matched. Check `postback_targets` below for a control whose "
                "argument looks like 'Page$2' — that is the pager. If none appears, the "
                "listing is single-page and the agent should process one page."
            )

        fixture_dir.mkdir(parents=True, exist_ok=True)
        listing_fixture = fixture_dir / "listing.html"
        listing_fixture.write_text(html, encoding="utf-8")
        report["listing_fixture"] = str(listing_fixture)

        if detail_sample and report.get("sample_item_href"):
            href = report["sample_item_href"]

            if href.strip().lower().startswith("javascript:"):
                # The decisive test for a postback-driven listing: click the
                # row and see where the browser ends up. If the URL changes,
                # a real detail page exists and tenders can be linked. If it
                # does not, the detail view has no address at all — which is
                # a product decision, not a parsing problem.
                report["detail_probe"] = await follow_postback_row(
                    page, best, url, fixture_dir, wait_ms
                )
            else:
                detail_url = urljoin(url, href)
                await page.goto(detail_url, wait_until="networkidle", timeout=90_000)
                await page.wait_for_timeout(wait_ms)

                detail_fixture = fixture_dir / "detail.html"
                detail_fixture.write_text(await page.content(), encoding="utf-8")

                report["detail_url"] = detail_url
                report["detail_fixture"] = str(detail_fixture)
                report["detail_text_sample"] = (await page.inner_text("body"))[:3000]
                report["attachment_hrefs"] = [
                    await a.get_attribute("href")
                    for a in (
                        await page.query_selector_all(
                            "a[href$='.pdf'], a[href*='Download'], a[href*='Attachment']"
                        )
                    )[:8]
                ]

        await browser.close()

    return report


async def follow_postback_row(page, item_selector: str, listing_url: str, fixture_dir, wait_ms: int) -> dict:
    """Click a postback-driven row and report where it lands.

    Answers the one question that decides whether tenders from this portal
    can carry a source link: does opening a tender produce a URL?
    """
    result: dict = {"before_url": page.url}

    try:
        elements = await page.query_selector_all(item_selector)
        sample = elements[1] if len(elements) > 1 else elements[0]
        link = await sample.query_selector("a[href], [onclick]")
        if link is None:
            return {**result, "outcome": "no clickable element in the row"}

        # A postback may navigate in place, or open a new tab.
        async with page.context.expect_page(timeout=5_000) as popup_info:
            await link.click()
        popup = await popup_info.value
        await popup.wait_for_load_state("networkidle", timeout=60_000)

        result["outcome"] = "opened a new tab"
        result["detail_url"] = popup.url
        (fixture_dir / "detail.html").write_text(await popup.content(), encoding="utf-8")
        result["detail_text_sample"] = (await popup.inner_text("body"))[:2000]
        await popup.close()
        return result

    except Exception:  # noqa: BLE001 - no popup appeared; it navigated in place
        pass

    try:
        await page.wait_for_load_state("networkidle", timeout=60_000)
        await page.wait_for_timeout(wait_ms)

        result["after_url"] = page.url
        result["url_changed"] = page.url != result["before_url"]
        result["outcome"] = (
            "navigated to a real URL — tenders can be linked"
            if result["url_changed"]
            else "stayed on the same URL — the detail view has no address of its own"
        )

        (fixture_dir / "detail.html").write_text(await page.content(), encoding="utf-8")
        result["detail_text_sample"] = (await page.inner_text("body"))[:2000]
    except Exception as exc:  # noqa: BLE001
        result["outcome"] = f"click failed: {type(exc).__name__}: {exc}"

    return result


async def collect_postback_targets(page, limit: int = 40) -> list[dict]:
    """List every `__doPostBack` / `window.open` call on the page.

    WebForms encodes both row navigation and pagination as
    `__doPostBack('<control id>', '<argument>')`. The argument is the tell:
    `Page$2` is the pager, while a row id or key is a tender link. Reading
    them beats guessing selectors, because neither has an href to match on.
    """
    return await page.evaluate(
        """(limit) => {
            const out = [];
            const seen = new Set();
            const nodes = document.querySelectorAll('a, input[type=submit], input[type=button], [onclick]');

            for (const node of nodes) {
                const raw = (node.getAttribute('href') || '') + ' ' + (node.getAttribute('onclick') || '');
                if (!/__doPostBack|WebForm_PostBackOptions|window\\.open/.test(raw)) continue;

                // A GridView LinkButton often renders as
                // WebForm_DoPostBackWithOptions(new WebForm_PostBackOptions("t","a",...))
                // rather than a bare __doPostBack('t','a'). Matching only the
                // bare form reports "no links" on a page full of them.
                const postback =
                    raw.match(/__doPostBack\\(\\s*['"]([^'"]*)['"]\\s*,\\s*['"]([^'"]*)['"]/) ||
                    raw.match(/WebForm_PostBackOptions\\(\\s*['"]([^'"]*)['"]\\s*,\\s*['"]([^'"]*)['"]/);
                const opened = raw.match(/window\\.open\\(\\s*['"]([^'"]+)['"]/);

                const entry = {
                    text: (node.innerText || node.value || '').trim().slice(0, 60),
                    id: node.id || null,
                    target: postback ? postback[1] : null,
                    argument: postback ? postback[2] : null,
                    opens_url: opened ? opened[1] : null,
                };

                const key = JSON.stringify(entry);
                if (seen.has(key)) continue;
                seen.add(key);

                entry.looks_like = entry.argument && /^Page\\$/.test(entry.argument)
                    ? 'pager'
                    : (entry.opens_url ? 'opens a URL' : 'row / action');
                out.push(entry);

                if (out.length >= limit) break;
            }
            return out;
        }""",
        limit,
    )


def describe_shape(value, depth: int = 0) -> object:
    """Summarize a JSON body's structure without dumping its contents."""
    if depth > 3:
        return "..."
    if isinstance(value, dict):
        return {k: describe_shape(v, depth + 1) for k, v in list(value.items())[:12]}
    if isinstance(value, list):
        return [describe_shape(value[0], depth + 1), f"... ({len(value)} items)"] if value else []
    return type(value).__name__


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe a tender portal's architecture.")
    parser.add_argument("--url", required=True, help="Listing URL to probe.")
    parser.add_argument("--detail-sample", action="store_true", help="Also open the first item.")
    parser.add_argument("--headed", action="store_true", help="Show the browser window.")
    parser.add_argument("--wait-ms", type=int, default=3000, help="Extra wait for late XHR.")
    parser.add_argument("--out", type=Path, help="Write the full JSON report here.")
    args = parser.parse_args()

    robots = check_robots(args.url)
    print("=== robots.txt ===")
    print(json.dumps(robots, indent=2, ensure_ascii=False))

    if robots.get("ok") and not robots.get("allowed_for_wildcard"):
        print(
            "\nrobots.txt disallows this path. Stop here and resolve access with the portal "
            "operator before scraping it.",
            file=sys.stderr,
        )
        return 2

    report = asyncio.run(
        probe(
            args.url,
            detail_sample=args.detail_sample,
            headless=not args.headed,
            wait_ms=args.wait_ms,
        )
    )
    report["robots"] = robots

    print("\n=== architecture ===")
    print(json.dumps(report["architecture"], indent=2, ensure_ascii=False))

    if report.get("warning"):
        print(f"\n!! {report['warning']}")

    if report.get("json_endpoints"):
        print("\n=== JSON endpoints (prefer these over selectors) ===")
        for call in report["json_endpoints"]:
            print(f"\n  {call['method']} {call['url']}  [{call['status']}]")
            if call.get("post_data"):
                print(f"    body: {call['post_data'][:300]}")
            print(f"    shape: {json.dumps(call['response_shape'], ensure_ascii=False)[:600]}")

    print("\n=== selectors ===")
    print(
        json.dumps(
            {
                "suggested_item_selector": report.get("suggested_item_selector"),
                "item_selector_matches": report.get("item_selector_matches"),
                "pager_candidates": report.get("pager_candidates"),
            },
            indent=2,
            ensure_ascii=False,
        )
    )

    if report.get("pager_note"):
        print(f"\n!! {report['pager_note']}")

    if report.get("postback_targets"):
        print("\n=== __doPostBack / window.open targets ===")
        for entry in report["postback_targets"]:
            print(
                f"  [{entry['looks_like']:>12}] text={entry['text']!r} "
                f"target={entry['target']!r} arg={entry['argument']!r} "
                f"opens={entry['opens_url']!r}"
            )

    if report.get("column_map"):
        print("\n=== column map (set the contract's col_* indexes from this) ===")
        print(json.dumps(report["column_map"], indent=2, ensure_ascii=False))

    if report.get("href_warning"):
        print(f"\n!! {report['href_warning']}")

    if report.get("sample_row_links"):
        print("\n=== first data row — every link, verbatim ===")
        for entry in report["sample_row_links"]:
            print(f"  text={entry['text']!r} id={entry['id']!r}")
            print(f"    href={entry['href']!r}")
            if entry.get("onclick"):
                print(f"    onclick={entry['onclick']!r}")

    if report.get("detail_probe"):
        print("\n=== following a row link ===")
        print(json.dumps(report["detail_probe"], indent=2, ensure_ascii=False)[:2000])

    if report.get("sample_item_text"):
        print("\n=== sample row/card text ===")
        print(report["sample_item_text"])

    print(f"\nFixtures: {report.get('listing_fixture')}")
    if report.get("detail_fixture"):
        print(f"          {report['detail_fixture']}")

    if args.out:
        args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nFull report: {args.out}")

    print("\nNext: paste the confirmed values into the portal's contract block and set verified=True.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
