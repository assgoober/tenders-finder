"""Explore the gov.il publications API.

The browser probe found gov.il is an SPA backed by a JSON API on
`openapi-gc.digital.gov.il` — the government ICT authority's gateway, with
"openapi" in the hostname. That is a far better integration target than CSS
selectors: it survives redesigns, returns structured data, is cheap to poll,
and needs no browser.

The listing call it made returned `{"total": 0, "results": []}`, and the page
said "אין תוצאות". Not because there are no tenders — because the query was
wrong. The SPA had normalised the URL's `publicationType=tenders` into
`Type=tenders`, and `Type` is a taxonomy filter whose valid values come from
`GetTypesList` (85 of them). "tenders" is not one. The collector type that
selects tender notices is almost certainly `rfp`.

This script settles that by asking the API directly:

  1. Is there a robots.txt on the API host? It is a different host from
     www.gov.il, so it carries its own policy and has to be checked
     separately.
  2. What are the valid `Type` values? Dump the ones that look
     tender-related.
  3. Which query actually returns rows? Several parameter combinations are
     tried and their `total` reported.
  4. What does one result record look like? That is what the agent maps into
     a TenderStub.

Uses only the standard library — no Playwright, no dependencies:

    python tools/probe_govil_api.py
    python tools/probe_govil_api.py --out govil-api.json

Read-only: it issues a handful of GET requests, the same ones the site's own
front end makes.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from urllib.robotparser import RobotFileParser

API_HOST = "https://openapi-gc.digital.gov.il"
API_BASE = f"{API_HOST}/pub/cio/govil/rest/collectors/v1/api/DataCollector"

USER_AGENT = "TendersFinderBot/0.1 (+https://tendersfinder.example/bot)"

# Collector types the SPA sends. `rfp` is the interesting one; the others are
# reports, job ads and public consultations sharing the same collector.
ALL_COLLECTOR_TYPES = ["reports", "rfp", "drushim", "publicsharing"]

# Query shapes to compare. The first is what the SPA sent and returned zero
# for; the rest narrow toward tenders. Reporting `total` for each shows which
# parameter was actually responsible.
QUERY_ATTEMPTS: list[tuple[str, list[tuple[str, str]]]] = [
    (
        "as the SPA sent it (Type=tenders) — expected to return 0",
        [("CollectorType", t) for t in ALL_COLLECTOR_TYPES]
        + [("Type", "tenders"), ("culture", "he")],
    ),
    (
        "same, minus the bogus Type filter",
        [("CollectorType", t) for t in ALL_COLLECTOR_TYPES] + [("culture", "he")],
    ),
    (
        "rfp only — the collector type that should mean tenders",
        [("CollectorType", "rfp"), ("culture", "he")],
    ),
    (
        "rfp only, first page explicitly",
        [("CollectorType", "rfp"), ("skip", "0"), ("limit", "10"), ("culture", "he")],
    ),
]

TENDER_WORDS = ("מכרז", "rfp", "tender", "רכש", "הצעות", "התקשרות")


#: Set from --client-id. The gateway rejects every call without it.
CLIENT_ID: str | None = None


def fetch(url: str, timeout: float = 45.0) -> tuple[int, object | str]:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if CLIENT_ID:
        headers["x-client-id"] = CLIENT_ID
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            try:
                return response.status, json.loads(body)
            except json.JSONDecodeError:
                return response.status, body[:2000]
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")[:2000]
    except Exception as exc:  # noqa: BLE001
        return 0, f"{type(exc).__name__}: {exc}"


def build_url(params: list[tuple[str, str]]) -> str:
    return f"{API_BASE}/GetResults?{urllib.parse.urlencode(params)}"


def check_api_robots() -> dict:
    """The API lives on its own host, so it carries its own robots policy."""
    robots_url = f"{API_HOST}/robots.txt"
    parser = RobotFileParser()
    parser.set_url(robots_url)
    try:
        parser.read()
    except Exception as exc:  # noqa: BLE001
        return {"url": robots_url, "error": f"{type(exc).__name__}: {exc}"}

    status, body = fetch(robots_url)

    # A 404 or an empty response is not a policy document. Reporting it as
    # one would be a false statement in the output, and the whole point of
    # this check is that the reader can trust what it says.
    has_body = isinstance(body, str) and bool(body.strip())
    is_policy = status == 200 and has_body and not body.lstrip().startswith("<")

    return {
        "url": robots_url,
        "http_status": status,
        "allowed_for_wildcard": parser.can_fetch("*", API_BASE),
        "looks_like_a_real_robots_txt": is_policy,
        "interpretation": (
            "No robots.txt on this host (404). Under the robots convention that means no "
            "restriction — but this API is gated by a client id, which is the control that "
            "actually governs access here."
            if status == 404
            else None
        ),
        "body": body[:1000] if isinstance(body, str) else "(JSON, not robots.txt)",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, help="Write the full JSON report here.")
    parser.add_argument("--culture", default="he", help="Language code (default: he).")
    parser.add_argument(
        "--client-id",
        help=(
            "Apigee client id for the gateway, sent as x-client-id. Request your own from "
            "the API operator — do not reuse the one embedded in the gov.il front end."
        ),
    )
    args = parser.parse_args()

    global CLIENT_ID
    CLIENT_ID = args.client_id
    if not CLIENT_ID:
        print(
            "No --client-id supplied. The gateway will reject every call with "
            "'Failed to resolve API Key variable request.header.x-client-id'. "
            "Running anyway so the failure is on the record.\n",
            file=sys.stderr,
        )

    report: dict = {"api_base": API_BASE}

    print("=== robots.txt on the API host ===")
    report["robots"] = check_api_robots()
    print(json.dumps({k: v for k, v in report["robots"].items() if k != "body"}, indent=2))
    if report["robots"].get("looks_like_a_real_robots_txt"):
        print("\n--- body ---")
        print(report["robots"]["body"])

    if report["robots"].get("allowed_for_wildcard") is False and report["robots"].get(
        "looks_like_a_real_robots_txt"
    ):
        print(
            "\nThe API host's robots.txt disallows this path. Stop and resolve access "
            "with the operator.",
            file=sys.stderr,
        )
        return 2

    # --- 2. Valid Type values -------------------------------------------------
    print("\n=== Type taxonomy — tender-related values ===")
    status, types = fetch(
        f"{API_BASE}/GetTypesList?"
        + urllib.parse.urlencode(
            [("collectionTypes", t) for t in ALL_COLLECTOR_TYPES] + [("culture", args.culture)]
        )
    )
    report["types_status"] = status

    if isinstance(types, list):
        report["type_count"] = len(types)
        matches = [
            entry
            for entry in types
            if any(word in str(entry.get("value", "")).lower() for word in TENDER_WORDS)
        ]
        report["tender_types"] = matches
        print(f"{len(types)} types total; {len(matches)} look tender-related:")
        for entry in matches:
            print(f"  key={entry.get('key')}  value={entry.get('value')}")
        if not matches:
            print("  none matched — showing the first 25 so you can pick by eye:")
            for entry in types[:25]:
                print(f"  key={entry.get('key')}  value={entry.get('value')}")
    else:
        print(f"Unexpected response ({status}): {str(types)[:400]}")

    # --- 3. Which query returns rows -----------------------------------------
    print("\n=== GetResults — which query actually returns tenders ===")
    attempts = []
    for label, params in QUERY_ATTEMPTS:
        url = build_url([(k, v if k != "culture" else args.culture) for k, v in params])
        status, payload = fetch(url)

        total = None
        count = None
        if isinstance(payload, dict):
            total = payload.get("total")
            results = payload.get("results")
            count = len(results) if isinstance(results, list) else None

        attempts.append(
            {"label": label, "url": url, "status": status, "total": total, "returned": count}
        )
        print(f"\n  {label}")
        print(f"    HTTP {status}   total={total}   returned={count}")
        if not isinstance(payload, dict):
            print(f"    body: {str(payload)[:300]}")

    report["attempts"] = attempts

    # --- 4. One record, in full ----------------------------------------------
    winner = next(
        (a for a in attempts if isinstance(a.get("total"), int) and a["total"] > 0), None
    )
    if winner:
        print(f"\n=== a result record from: {winner['label']} ===")
        _, payload = fetch(winner["url"])
        results = payload.get("results") if isinstance(payload, dict) else None
        if results:
            record = results[0]
            report["sample_record"] = record
            report["record_fields"] = sorted(record) if isinstance(record, dict) else None
            print(json.dumps(record, indent=2, ensure_ascii=False)[:3000])
            if isinstance(record, dict):
                print("\nFields:", ", ".join(sorted(record)))
    else:
        print(
            "\nNo query returned rows. Pick a tender-related Type key from the taxonomy above "
            "and retry with it, or widen the date/status filters — the collector may default "
            "to open notices only."
        )

    if args.out:
        args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nFull report: {args.out}")

    print(
        "\nNext: whichever query returned rows becomes the gov.il agent's source, and the "
        "record fields above become its mapping. An API beats selectors — no browser, no "
        "pagination guesswork, and it survives a redesign."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
