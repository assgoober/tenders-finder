"""Agent for TED - Tenders Electronic Daily, the EU's procurement journal.

    https://api.ted.europa.eu/v3/notices/search

This is the first source in the project that is an **API rather than a
portal**, and that difference is the whole design. There is no browser, no
`__VIEWSTATE`, no postback and no pagination guard, because there is nothing
to guard: the service returns JSON and hands back a continuation token.

## Probe results - 2026-09-11

Run live against the production endpoint, not read from documentation:

* **No authentication.** `POST /v3/notices/search` answered anonymously.
* **Volume.** `classification-cpv IN (35000000)` - the defence and security
  CPV division - returns **100,063** notices in total and **1,167** published
  in the last thirty days. The whole corpus at the time of this probe was
  ~180 rows from one Israeli portal.
* **The request shape is not guessable, and the API says so.** Omitting
  `fields` fails with `{"field": "fields", "message": "must not be empty"}`,
  and an unsupported name is answered with the full list of the 1,830 that
  are supported. Both errors are precise; three attempts here were spent
  guessing at a query the service was willing to explain.
* **Multilingual fields arrive as `{lang: [values]}`**, not as strings -
  `notice-title`, `description-lot` and `buyer-name` all do. Treating them as
  text stores a Python dict repr in the database.
* **A deadline is two fields**, `...-date-lot` and `...-time-lot`, each a
  list, each carrying its own UTC offset (`2026-10-13+02:00`, `09:00:00+02:00`).
  Joining them is the only way to get the real closing moment; taking the date
  alone loses the time exactly as the BALAM parser once did.
* **Pagination is a token**, `iterationNextToken`, not a page number.

## What this source gives us that BALAM does not

A document. BALAM publishes a listing row and puts the specification behind a
supplier login, so PEGS extraction there has nothing to read. Every TED notice
exposes its full XML at a stable URL, so this is the first source where the
extraction ladder is given the material it was built for.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time
from typing import Any, Iterable

import httpx

from ..config import Settings
from .base import FetchedTender, TenderSourceAgent, TenderStub
from .registry import register_agent

logger = logging.getLogger(__name__)

#: Language preference when a field arrives as {lang: [values]}. English
#: first because the matching corpus is embedded in one vector space and a
#: mixture of 24 languages dilutes it; the original language is the fallback
#: rather than dropping a notice that has no English rendering.
_LANGUAGE_PREFERENCE = ("eng", "ENG", "en")

#: Requested per notice. Deliberately short: the API exposes 1,830 field
#: names, and asking for everything moves megabytes per page to populate
#: columns nothing reads.
TED_FIELDS = [
    "publication-number",
    "notice-title",
    "buyer-name",
    "buyer-country",
    "publication-date",
    "deadline-receipt-tender-date-lot",
    "deadline-receipt-tender-time-lot",
    # A restricted procedure closes its FIRST gate on a request to
    # participate, not on a tender. Reading only the tender deadline
    # reported None for five notices in a forty-notice sample and
    # silently dropped the date a bidder actually has to hit.
    "deadline-receipt-request-date-lot",
    "deadline-receipt-request-time-lot",
    "classification-cpv",
    "description-lot",
    "notice-type",
    "procedure-type",
    "estimated-value-lot",
    "estimated-value-cur-lot",
    "place-of-performance-country-lot",
    # Defence-specific, and the reason this source is worth more to us than a
    # generic aggregator: a notice that demands clearance is one an exporter
    # must know about early or not bid at all.
    "security-clearance-lot",
    "links",
]


# --- Pure normalisation (no network; every one of these is unit-tested) -----

def pick_language(value: Any, prefer: Iterable[str] = _LANGUAGE_PREFERENCE) -> str | None:
    """Collapse TED's `{lang: [values]}` shape to one string.

    Returns None rather than an empty string when there is nothing to pick, so
    a caller can tell "absent" from "present but blank": the pipeline stores
    None, while a blank would be embedded as real text.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, list):
        for item in value:
            picked = pick_language(item, prefer)
            if picked:
                return picked
        return None
    if isinstance(value, dict):
        for lang in prefer:
            if lang in value:
                picked = pick_language(value[lang], prefer)
                if picked:
                    return picked
        # No English rendering. Take the first with content rather than
        # discarding it: a German-only defence notice is still a defence
        # notice.
        for item in value.values():
            picked = pick_language(item, prefer)
            if picked:
                return picked
    return None


def first_value(value: Any) -> Any:
    """TED returns most scalars inside a list. Unwrap one level."""
    if isinstance(value, list):
        return value[0] if value else None
    return value


def parse_ted_date(value: Any) -> date | None:
    """`2026-10-13+02:00` -> date. The offset belongs to the time, not here."""
    raw = first_value(value)
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return date.fromisoformat(raw.strip()[:10])
    except ValueError:
        return None


def combine_deadline(date_value: Any, time_value: Any) -> datetime | None:
    """Join TED's split deadline into one datetime.

    The two halves are separate fields and each carries its own offset. Using
    the date alone silently moves every deadline to midnight - the same defect
    the BALAM parser had when it read a time-first Hebrew deadline and kept
    only the date: a perfectly valid timestamp, sixteen hours early.
    """
    day = parse_ted_date(date_value)
    if day is None:
        return None

    raw_time = first_value(time_value)
    if not isinstance(raw_time, str) or not raw_time.strip():
        # A published date with no published time. Stored as given rather than
        # invented: a guessed closing hour is worse than an absent one.
        return datetime.combine(day, time(0, 0))

    try:
        parsed = time.fromisoformat(raw_time.strip())
    except ValueError:
        return datetime.combine(day, time(0, 0))

    return datetime.combine(day, parsed)


def resolve_deadline(notice: dict) -> tuple[datetime | None, str | None]:
    """The next date a bidder must hit, and which gate it is.

    An open procedure closes on the tender deadline. A restricted one
    closes first on a request to participate, weeks earlier, and missing
    that gate ends the bid just as finally. Returning the kind alongside
    the date keeps the two distinguishable downstream instead of
    flattening them into one unlabelled timestamp.
    """
    tender = combine_deadline(
        notice.get("deadline-receipt-tender-date-lot"),
        notice.get("deadline-receipt-tender-time-lot"),
    )
    if tender is not None:
        return tender, "tender"

    request = combine_deadline(
        notice.get("deadline-receipt-request-date-lot"),
        notice.get("deadline-receipt-request-time-lot"),
    )
    if request is not None:
        return request, "request-to-participate"

    return None, None

def notice_url(links: Any, publication_number: str | None) -> str | None:
    """The human-readable notice page.

    Derived from the publication number rather than read out of `links`,
    because it is stable, short, and the same string a buyer would paste into
    an email.
    """
    if publication_number:
        return f"https://ted.europa.eu/en/notice/-/detail/{publication_number}"
    html = (links or {}).get("html") if isinstance(links, dict) else None
    return pick_language(html) if html else None


def xml_url(links: Any) -> str | None:
    """The multilingual XML rendering - the document PEGS extraction reads."""
    if not isinstance(links, dict):
        return None
    xml = links.get("xml")
    if isinstance(xml, dict):
        # MUL is the multilingual document; prefer it, then anything present.
        return xml.get("MUL") or next(iter(xml.values()), None)
    return None


@register_agent
class TedAgent(TenderSourceAgent):
    """EU-wide defence procurement, over the official API."""

    key = "ted-eu"
    display_name = "TED - Tenders Electronic Daily (EU)"
    base_url = "https://ted.europa.eu"
    country = None            # EU-wide; the buyer's country travels per notice
    sector = "defense"

    SEARCH_URL = "https://api.ted.europa.eu/v3/notices/search"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self._cpv = settings.ted_cpv_codes
        self._lookback_days = settings.ted_lookback_days
        self._page_size = settings.ted_page_size
        self._notice_types = settings.ted_notice_types
        self._cpv_field = settings.ted_cpv_field

    # --- Query ---------------------------------------------------------------

    def build_query(self) -> str:
        """The expert query, built from configuration rather than hardcoded.

        Sorted newest-first so a run capped by `limit` keeps the notices a bid
        team can still act on, instead of an arbitrary slice of an archive
        that reaches back to 2016.
        """
        cpv = ", ".join(str(c) for c in self._cpv)
        clauses = [f"{self._cpv_field} IN ({cpv})"]
        if self._notice_types:
            kinds = ", ".join(self._notice_types)
            clauses.append(f"notice-type IN ({kinds})")
        clauses.append(f"publication-date>=today(-{self._lookback_days})")
        return " AND ".join(clauses) + " SORT BY publication-date DESC"

    # --- Navigation ----------------------------------------------------------

    async def discover(self, *, limit: int = 50) -> list[TenderStub]:
        stubs: list[TenderStub] = []
        token: str | None = None
        query = self.build_query()

        async with httpx.AsyncClient(
            timeout=self.settings.scrape_timeout_seconds,
            headers={"User-Agent": self.settings.scrape_user_agent},
        ) as client:
            while len(stubs) < limit:
                payload: dict[str, Any] = {
                    "query": query,
                    "fields": TED_FIELDS,
                    "limit": min(self._page_size, limit - len(stubs)),
                }
                if token:
                    payload["iterationNextToken"] = token

                response = await self.with_backoff(
                    lambda: client.post(self.SEARCH_URL, json=payload),
                    description="TED search",
                )
                # The API answers a bad request with the precise reason.
                # Losing that body to a bare status code is how three attempts
                # were spent guessing at a query it was willing to explain.
                if response.status_code >= 400:
                    raise RuntimeError(
                        f"TED search returned {response.status_code}: {response.text[:400]}"
                    )
                body = response.json()

                notices = body.get("notices") or []
                for notice in notices:
                    stub = self.build_stub(notice)
                    if stub is not None:
                        stubs.append(stub)

                token = body.get("iterationNextToken")
                if not token or not notices:
                    break

        if not stubs:
            logger.warning(
                "[%s] no notices for query %r - either nothing was published in "
                "the window or the CPV configuration is wrong",
                self.key, query,
            )
        return stubs[:limit]

    def build_stub(self, notice: dict) -> TenderStub | None:
        """One API notice -> one TenderStub. Pure; tested without a network."""
        reference = notice.get("publication-number")
        if not reference:
            # Without the publication number there is no stable external_id
            # and no URL, so the row could never be deduplicated or linked.
            return None
        reference = str(reference)

        title = pick_language(notice.get("notice-title"))
        links = notice.get("links")
        published = parse_ted_date(notice.get("publication-date"))
        deadline, deadline_kind = resolve_deadline(notice)

        return TenderStub(
            external_id=reference,
            title=title or reference,
            source_url=notice_url(links, reference) or "",
            buyer_name=pick_language(notice.get("buyer-name")),
            publication_date=datetime.combine(published, time(0, 0)) if published else None,
            deadline=deadline,
            listing_metadata={
                "portal": self.key,
                "reference_number": reference,
                "notice_type": notice.get("notice-type"),
                "procedure_type": first_value(notice.get("procedure-type")),
                "cpv_codes": notice.get("classification-cpv") or [],
                "buyer_country": first_value(notice.get("buyer-country")),
                "performance_country": first_value(
                    notice.get("place-of-performance-country-lot")
                ),
                "estimated_value": first_value(notice.get("estimated-value-lot")),
                "estimated_value_currency": first_value(
                    notice.get("estimated-value-cur-lot")
                ),
                "security_clearance": first_value(notice.get("security-clearance-lot")),
                "deadline_kind": deadline_kind,
                "description": pick_language(notice.get("description-lot")),
                "xml_url": xml_url(links),
                # The change detector the pipeline incremental skip reads. TED
                # has no "last updated" column, so the publication number plus
                # the deadline stand in: a corrected notice is republished
                # under a new number.
                "raw_row": f"{reference} | {title or ''} | {deadline.isoformat() if deadline else ''}",
            },
        )

    # --- Extraction ----------------------------------------------------------

    async def fetch(self, stub: TenderStub) -> FetchedTender:
        """Pull the notice's own XML, which is the document PEGS reads.

        Falls back to the listing text rather than failing: a notice whose XML
        cannot be retrieved is still worth storing with its title and
        deadline, which is most of what an alert needs.
        """
        meta = stub.listing_metadata or {}
        url = meta.get("xml_url")
        text = ""

        if url:
            async with httpx.AsyncClient(
                timeout=self.settings.scrape_timeout_seconds,
                headers={"User-Agent": self.settings.scrape_user_agent},
                follow_redirects=True,
            ) as client:
                try:
                    response = await self.with_backoff(
                        lambda: client.get(url), description=f"TED notice XML {url}"
                    )
                    if response.status_code < 400:
                        text = response.text
                    else:
                        logger.warning(
                            "[%s] notice XML for %s returned %d",
                            self.key, stub.external_id, response.status_code,
                        )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "[%s] could not fetch notice XML for %s: %s",
                        self.key, stub.external_id, exc,
                    )

        if not text:
            text = "\n".join(p for p in (stub.title, meta.get("description")) if p)

        return FetchedTender(stub=stub, raw_text=text, documents=[])
