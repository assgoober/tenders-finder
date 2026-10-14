"""Agent for gov.il — the unified Israeli government publications portal.

Israeli ministries publish tender notices through gov.il's shared
publications collector rather than each running its own portal, so one agent
covers many buyers. Ministry of Defense procurement additionally runs on its
own system (online.mod.gov.il), which needs a separate agent.

## Read this before enabling the agent

The parsing, normalization and dedup logic below is written and tested. The
**portal contract** — the listing URL shape, the CSS selectors, and the
pagination control — is NOT verified against the live site: the environment
this was written in has no network route to gov.il, so nothing here was
observed. Those values are declared in one block (`GovIlContract`) and
nowhere else.

Run `python -m tools.probe_govil` from a machine that can reach gov.il. It
checks robots.txt, detects whether the listing is server-rendered or fetched
over XHR, reports which selectors actually match, and saves a fixture. Fill
the results into `GovIlContract`, drop the fixture into
`tests/fixtures/govil/`, and the agent is live.

Until then `GovIlContract.verified` is False and the agent refuses to run, so
it cannot silently return zero tenders and look like a quiet week in
procurement.

## Legal

gov.il publishes tender notices for the express purpose of reaching bidders,
and this agent reads only those public pages. Still confirm the current
terms of use and robots.txt before running it in production — the probe
reports both — and leave the conservative rate limits below alone.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import time
from urllib.parse import urljoin

from ..config import Settings
from .base import FetchedTender, TenderStub
from .hebrew import (
    clean_text,
    extract_tender_number,
    parse_amount,
    parse_israeli_date,
    parse_israeli_datetime,
    parse_status,
)
from .playwright_agent import PlaywrightPortalAgent
from .registry import register_agent

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GovIlContract:
    """Everything about gov.il that has to be observed rather than reasoned.

    Isolated deliberately: when the portal is redesigned, this block is the
    only thing that changes. Nothing below reads the DOM directly.
    """

    #: Flip to True only after `tools/probe_govil.py` confirms these against
    #: the live site. The agent refuses to run while this is False.
    verified: bool = False

    #: Listing path. `publicationType` selects tender notices; leaving
    #: officeId unset returns every ministry.
    #: UNVERIFIED — a guess at gov.il's route, never loaded. gov.il has been
    #: rebuilt more than once and the tender collector has lived at several
    #: paths. Probe these in order and keep whichever returns rows:
    #:
    #:   /he/collectors/publications?publicationType=tenders
    #:   /he/departments/publications?publicationType=tenders
    #:   /he/departments/topics/tenders-sub/govil-landing-page
    #:
    #: If a run reports zero items, the probe now dumps the page's
    #: tender-related links — the real listing URL is usually one of them.
    listing_path: str = "/he/collectors/publications?publicationType=tenders"

    #: One card in the results list.
    item_selector: str = "[data-testid='publication-card'], .publication-item, li.result-item"
    title_selector: str = "h2, h3, .publication-title"
    link_selector: str = "a[href]"
    #: Fields carried on the listing card. Read opportunistically — anything
    #: missing is recovered from the detail page or left null.
    publication_date_selector: str = ".publication-date, time"
    deadline_selector: str = ".deadline, .last-date"
    status_selector: str = ".status, .publication-status"
    buyer_selector: str = ".office-name, .publisher"

    next_page_selector: str | None = "a[rel='next'], .pagination-next:not([disabled])"

    detail_body_selector: str = "main, .publication-content, #content"
    attachment_selector: str = "a[href$='.pdf'], a[href*='BlobFolder']"

    #: Ministries worth restricting to, by gov.il office id. Empty means all.
    #: Populate from the probe's office listing.
    office_ids: tuple[str, ...] = field(default=())


CONTRACT = GovIlContract()

#: Field labels used on gov.il detail pages. These double as the boundaries
#: for `_value_after`: a value runs until the next label starts. Add a label
#: here when the probe reveals one, or its value will be appended to whatever
#: field precedes it.
FIELD_LABELS: tuple[str, ...] = (
    "משרד",
    "עורך המכרז",
    "סוג מכרז",
    "תאריך פרסום",
    "מועד אחרון להגשה",
    "מועד אחרון",
    "תאריך אחרון",
    "מועד הגשה",
    "אומדן",
    "סטטוס",
    "תיאור",
    "מסמכים",
    "איש קשר",
)


class PortalContractUnverified(RuntimeError):
    """Raised when the agent is run before its selectors have been confirmed."""


@register_agent
class GovIlTendersAgent(PlaywrightPortalAgent):
    """Israeli government tenders published through gov.il."""

    key = "govil"
    display_name = "gov.il — פרסומי מכרזים ממשלתיים"
    base_url = "https://www.gov.il"
    country = "IL"
    # gov.il carries every ministry, defense and civilian alike, which is a
    # good fit for the content-agnostic model: the same agent feeds a defense
    # RFP and a municipal works tender through one pipeline.
    sector = "mixed"

    # Israeli government sites sit behind aggressive bot protection and this
    # is a single shared national portal, so the polite ceiling is low.
    # Tenders are published on a daily cadence; there is nothing to gain from
    # hammering it.
    max_pages = 10

    def __init__(self, settings: Settings, contract: GovIlContract | None = None) -> None:
        super().__init__(settings)
        self.contract = contract or CONTRACT

        # Feed the contract into the generic Playwright machinery.
        self.listing_path = self.contract.listing_path
        self.listing_item_selector = self.contract.item_selector
        self.listing_title_selector = self.contract.title_selector
        self.listing_link_selector = self.contract.link_selector
        self.pagination_next_selector = self.contract.next_page_selector
        self.detail_body_selector = self.contract.detail_body_selector
        self.detail_attachment_selector = self.contract.attachment_selector

    # --- Guard ---------------------------------------------------------------

    def _require_verified_contract(self) -> None:
        if not self.contract.verified:
            raise PortalContractUnverified(
                "The gov.il portal contract has not been verified against the live site. "
                "Run `python -m tools.probe_govil` from a machine with network access to "
                "gov.il, fill the results into GovIlContract, and set verified=True. "
                "Running with unconfirmed selectors would return zero tenders and be "
                "indistinguishable from a week with no publications."
            )

    # --- Navigation ----------------------------------------------------------

    async def discover(self, *, limit: int = 50) -> list[TenderStub]:
        self._require_verified_contract()
        stubs = await super().discover(limit=limit)

        if not stubs:
            # An empty listing on a national portal is far more likely to be a
            # broken selector than a genuinely empty day.
            logger.warning(
                "[%s] listing returned no tenders — re-run tools/probe_govil.py, "
                "the portal markup has probably changed",
                self.key,
            )

        return stubs

    async def _read_listing_page(self, page) -> list[TenderStub]:
        """Read one page of results, keeping the fields the card exposes.

        The listing carries deadline and status for free. Taking them here
        means a tender is usable — and sortable by urgency — even if its
        detail page later fails to fetch.
        """
        stubs: list[TenderStub] = []

        for item in await page.query_selector_all(self.contract.item_selector):
            link = await item.query_selector(self.contract.link_selector)
            href = await link.get_attribute("href") if link else None
            if not href:
                continue

            source_url = urljoin(self.base_url, href)
            title = clean_text(await self._text(item, self.contract.title_selector)) or ""
            if not title and link:
                title = clean_text(await link.inner_text()) or source_url

            stub = self.build_stub(
                source_url=source_url,
                title=title,
                buyer_text=await self._text(item, self.contract.buyer_selector),
                publication_text=await self._text(item, self.contract.publication_date_selector),
                deadline_text=await self._text(item, self.contract.deadline_selector),
                status_text=await self._text(item, self.contract.status_selector),
            )
            stubs.append(stub)

        return stubs

    @staticmethod
    async def _text(scope, selector: str) -> str | None:
        if not selector:
            return None
        element = await scope.query_selector(selector)
        if element is None:
            return None
        return await element.inner_text()

    # --- Normalization (pure, fully testable without a browser) --------------

    def build_stub(
        self,
        *,
        source_url: str,
        title: str,
        buyer_text: str | None = None,
        publication_text: str | None = None,
        deadline_text: str | None = None,
        status_text: str | None = None,
    ) -> TenderStub:
        """Turn raw listing strings into a normalized stub.

        Kept free of Playwright types so the whole normalization path is
        testable against saved fixtures rather than a live browser.
        """
        title = clean_text(title) or source_url

        return TenderStub(
            external_id=self.external_id_from(source_url, title),
            title=title,
            source_url=source_url,
            buyer_name=clean_text(buyer_text),
            publication_date=self._as_datetime(parse_israeli_date(publication_text)),
            # Submission deadlines are stated as a wall-clock time in Israel.
            # Where the card gives only a date, the time stays midnight rather
            # than being rounded up to end-of-day — a deadline invented an
            # extra 24 hours wide is how a team submits a day late.
            deadline=parse_israeli_datetime(deadline_text),
            listing_metadata={
                "raw_status": clean_text(status_text),
                "normalized_status": parse_status(status_text),
                "raw_deadline": clean_text(deadline_text),
                "portal": self.key,
            },
        )

    def external_id_from(self, source_url: str, title: str | None = None) -> str:
        """Stable dedup key.

        Prefers the buyer's own tender number, which survives a URL change and
        is the identifier a BD team actually quotes. Falls back to the URL
        slug — gov.il publication URLs carry a stable id segment.
        """
        reference = extract_tender_number(title)
        if reference:
            return reference
        return self.external_id_from_url(source_url)

    def enrich(self, fetched: FetchedTender) -> FetchedTender:
        """Recover fields from the detail page that the listing card omitted.

        This runs before LLM extraction and only fills gaps — cheap
        deterministic parsing first, so the model is not asked to re-derive a
        date the page states plainly.
        """
        stub = fetched.stub
        body = fetched.raw_text

        if stub.deadline is None:
            for label in ("מועד אחרון להגשה", "מועד אחרון", "תאריך אחרון", "מועד הגשה"):
                deadline = self._value_after(body, label)
                if deadline:
                    stub.deadline = parse_israeli_datetime(deadline)
                    if stub.deadline:
                        break

        if stub.publication_date is None:
            published = self._value_after(body, "תאריך פרסום")
            if published:
                stub.publication_date = self._as_datetime(parse_israeli_date(published))

        if not stub.buyer_name:
            stub.buyer_name = clean_text(
                self._value_after(body, "משרד") or self._value_after(body, "עורך המכרז")
            )

        amount, currency = parse_amount(self._value_after(body, "אומדן"))
        if amount is not None:
            stub.listing_metadata["estimated_value"] = amount
            stub.listing_metadata["currency"] = currency

        if stub.listing_metadata.get("normalized_status") is None:
            stub.listing_metadata["normalized_status"] = parse_status(body)

        return fetched

    async def fetch(self, stub: TenderStub) -> FetchedTender:
        self._require_verified_contract()
        return self.enrich(await super().fetch(stub))

    @classmethod
    def _value_after(cls, text: str | None, label: str, *, window: int = 160) -> str | None:
        """Grab the text following a Hebrew field label.

        gov.il detail pages present metadata as label/value pairs that are not
        reliably marked up as such, so this reads a bounded window after the
        label rather than trusting a DOM relationship that may not exist.

        The window is then cut at the next known field label. Line breaks are
        not usable as the boundary: `clean_text` collapses all whitespace
        (deliberately — RTL markup is full of stray newlines mid-sentence), so
        by the time this runs the page is one long line and an uncut window
        would swallow the following field's value.
        """
        cleaned = clean_text(text)
        if not cleaned:
            return None

        index = cleaned.find(label)
        if index == -1:
            return None

        start = index + len(label)
        tail = cleaned[start : start + window].lstrip(" :־-")

        cut = len(tail)
        for other in FIELD_LABELS:
            if other == label:
                continue
            position = tail.find(other)
            if position != -1:
                cut = min(cut, position)

        return tail[:cut].strip() or None

    @staticmethod
    def _as_datetime(value):
        """Publication dates carry no meaningful time of day."""
        if value is None:
            return None
        return parse_israeli_datetime(value.strftime("%d/%m/%Y"), default_time=time(0, 0))
