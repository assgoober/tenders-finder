"""Agent for the Israeli Ministry of Defense e-commerce portal (BALAM list).

    https://www.online.mod.gov.il/Online2016/Pages/General/Balam/BalamList.aspx?Reset=1

This is the primary Israeli defense procurement source and the closest single
portal to the product's core use case — a BD team at a defense contractor
checks this list by hand today.

## What the URL already tells us

`Online2016/Pages/.../BalamList.aspx` is ASP.NET WebForms, and that dictates
the navigation model. Three consequences, all handled below:

1. **Pagination is a postback, not a link.** The pager emits
   `javascript:__doPostBack('...','Page$2')`. The URL never changes, so pages
   cannot be requested directly, cannot be fetched in parallel, and cannot be
   resumed from the middle — the agent must walk them in order.
2. **`__VIEWSTATE` and `__EVENTVALIDATION` must round-trip** on every
   postback. This is the reason the agent drives a real browser instead of
   issuing HTTP requests: reproducing WebForms state by hand is brittle and
   breaks on any server-side change. The cost is speed; the benefit is that
   it keeps working.
3. **Filter state lives in the server session**, which is what `?Reset=1` is
   for. Entering without it can return the previous session's filtered view,
   so every run enters through the reset URL for a deterministic result set.

## Read this before enabling the agent

The parsing and pagination logic is written and tested. The **portal
contract** — the table and row selectors, the column order, the pager and the
detail link — is NOT verified against the live site: this was built in an
environment with no network route to `online.mod.gov.il`, so nothing here was
observed.

Run `python -m tools.probe_portal --url "<BALAM URL>" --detail-sample` from a
machine that can reach it. Fill the results into `BalamContract` and set
`verified=True`. Until then the agent refuses to run rather than silently
returning zero tenders.

## Operational notes

* Access may be restricted by geography. If the probe times out or returns a
  block page from outside Israel, run the scraper from an Israeli egress.
* This is a defense ministry system. Confirm the portal's terms of use before
  running it in production, keep the conservative rate limits below, and do
  not attempt to reach anything behind its supplier login — this agent reads
  the public list only.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from urllib.parse import urljoin

from ..config import Settings
from .base import FetchedTender, TenderStub
from .govil import PortalContractUnverified
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
class BalamContract:
    """Everything about the BALAM list that must be observed, not reasoned.

    When the portal is redesigned, this is the only block that changes.
    """

    #: Set True only after tools/probe_portal.py confirms these values.
    verified: bool = False

    #: Reset=1 clears server-side session filters. Always enter through it.
    listing_path: str = "/Online2016/Pages/General/Balam/BalamList.aspx?Reset=1"

    #: The GridView and its data rows. WebForms GridViews render header and
    #: pager as <tr> too, so the row selector must exclude them.
    table_selector: str = "table[id*='grd'], table[id*='gv'], table.GridView"
    row_selector: str = "tr:not(.GridHeader):not(.pager):not(.GridPager)"

    #: Zero-based column indexes within a row. WebForms tables are positional
    #: and rarely carry useful class names, so columns are read by position.
    #: Verify against a real row before enabling — a wrong index silently
    #: swaps two fields rather than failing.
    col_reference: int = 0
    col_title: int = 1
    col_buyer: int | None = 2
    col_publication_date: int | None = 3
    col_deadline: int | None = 4
    col_status: int | None = 5

    #: Link out of a row to the tender's own page.
    detail_link_selector: str = "a[href*='Balam'], a[id*='lnk']"

    #: Pager container. Individual page links are matched by their text.
    pager_selector: str = ".GridPager, tr.pager, [id*='Pager']"

    detail_body_selector: str = "form#aspnetForm, body"
    attachment_selector: str = "a[href$='.pdf'], a[href*='Download'], a[href*='Attachment']"

    #: WebForms postbacks re-render the whole page; this is how long to wait
    #: for the row set to actually change before deciding paging is finished.
    postback_timeout_ms: int = 20_000


CONTRACT = BalamContract()


@register_agent
class ModBalamAgent(PlaywrightPortalAgent):
    """Israeli Ministry of Defense published tender list."""

    key = "mod-balam"
    display_name = 'משרד הביטחון — רשימת מכרזים (בל"מ)'
    base_url = "https://www.online.mod.gov.il"
    country = "IL"
    sector = "defense"

    # A single ministry system, walked serially because of postback paging.
    # There is nothing to gain from pressing it harder.
    max_pages = 20

    def __init__(self, settings: Settings, contract: BalamContract | None = None) -> None:
        super().__init__(settings)
        self.contract = contract or CONTRACT
        self.listing_path = self.contract.listing_path
        self.detail_body_selector = self.contract.detail_body_selector
        self.detail_attachment_selector = self.contract.attachment_selector
        # Pagination is handled by the WebForms-aware override below, so the
        # generic link-clicking path is switched off.
        self.pagination_next_selector = None

    # --- Guard ---------------------------------------------------------------

    def _require_verified_contract(self) -> None:
        if not self.contract.verified:
            raise PortalContractUnverified(
                "The MOD BALAM portal contract has not been verified against the live site. "
                'Run `python -m tools.probe_portal --url '
                '"https://www.online.mod.gov.il/Online2016/Pages/General/Balam/BalamList.aspx?Reset=1" '
                "--detail-sample` from a machine that can reach it, fill the results into "
                "BalamContract, and set verified=True. Running with unconfirmed selectors "
                "would return zero tenders, which is indistinguishable from a week with no "
                "publications."
            )

    async def discover(self, *, limit: int = 50) -> list[TenderStub]:
        self._require_verified_contract()
        stubs = await super().discover(limit=limit)

        if not stubs:
            logger.warning(
                "[%s] BALAM list returned no rows — re-run tools/probe_portal.py; "
                "either the markup changed or the request was blocked (this portal may "
                "require an Israeli egress)",
                self.key,
            )

        return stubs

    async def fetch(self, stub: TenderStub) -> FetchedTender:
        self._require_verified_contract()
        return self.enrich(await super().fetch(stub))

    # --- Listing -------------------------------------------------------------

    async def _read_listing_page(self, page) -> list[TenderStub]:
        """Read one GridView page.

        Rows are read positionally: WebForms tables rarely carry meaningful
        class names on cells, so column indexes come from the contract.
        """
        stubs: list[TenderStub] = []
        rows = await self._data_rows(page)

        for row in rows:
            cells = await row.query_selector_all("td")
            if len(cells) < 2:
                # Header, pager, or spacer row.
                continue

            texts = [clean_text(await cell.inner_text()) for cell in cells]

            link = await row.query_selector(self.contract.detail_link_selector)
            href = await link.get_attribute("href") if link else None
            source_url = self._detail_url(href)
            if source_url is None:
                # A row with no navigable link is unusable — the source URL
                # is what makes the extraction auditable, so it is required.
                logger.debug("[%s] skipping row with no detail link", self.key)
                continue

            stubs.append(
                self.build_stub(
                    source_url=source_url,
                    reference=self._column(texts, self.contract.col_reference),
                    title=self._column(texts, self.contract.col_title) or "",
                    buyer_text=self._column(texts, self.contract.col_buyer),
                    publication_text=self._column(texts, self.contract.col_publication_date),
                    deadline_text=self._column(texts, self.contract.col_deadline),
                    status_text=self._column(texts, self.contract.col_status),
                    row_text=" | ".join(t for t in texts if t),
                )
            )

        return stubs

    async def _data_rows(self, page) -> list:
        table = await page.query_selector(self.contract.table_selector)
        scope = table or page
        return await scope.query_selector_all(self.contract.row_selector)

    def _detail_url(self, href: str | None) -> str | None:
        """Resolve a row's link, rejecting javascript postback links.

        Some GridViews link rows through `__doPostBack` rather than an href.
        Such a row has no addressable URL, and a tender without a link back to
        its source cannot be verified by a bid team — so it is skipped and
        reported rather than stored with a fabricated URL. If the probe shows
        the portal does this, the fix is to capture the id from the postback
        argument and build the real detail URL here.
        """
        if not href:
            return None
        if href.strip().lower().startswith("javascript:"):
            return None
        return urljoin(self.base_url, href)

    @staticmethod
    def _column(texts: list[str | None], index: int | None) -> str | None:
        if index is None or index < 0 or index >= len(texts):
            return None
        return texts[index]

    # --- WebForms pagination -------------------------------------------------

    async def _go_to_next_page(self, page) -> bool:
        """Advance the GridView by postback.

        Three things make this different from clicking a "next" link:

        * The URL does not change, so navigation waits have nothing to key on.
          Instead this snapshots the current first row and waits for it to
          change — the only reliable signal that the postback landed.
        * Page links are numbered, not "next", so the successor is located by
          its number.
        * A WebForms pager renders `...` ellipsis links that jump a block of
          pages. Matching by exact text avoids following one and skipping
          rows silently.

        Kept here rather than in a shared base until a second WebForms portal
        exists to generalize from.
        """
        pager = await page.query_selector(self.contract.pager_selector)
        if pager is None:
            return False

        current = await self._current_page_number(pager)
        target = str(current + 1)

        next_link = None
        for link in await pager.query_selector_all("a"):
            if (clean_text(await link.inner_text()) or "") == target:
                next_link = link
                break

        if next_link is None:
            return False

        before = await self._first_row_signature(page)

        await self.with_backoff(lambda: next_link.click(), description=f"postback to page {target}")

        try:
            await page.wait_for_function(
                """([selector, previous]) => {
                    const row = document.querySelector(selector + ' tr:nth-of-type(2)');
                    return row && row.innerText.trim() !== previous;
                }""",
                arg=[self.contract.table_selector, before],
                timeout=self.contract.postback_timeout_ms,
            )
        except Exception:  # noqa: BLE001
            # The rows did not change. Treat it as the end of the list rather
            # than re-ingesting the same page under a new page number.
            logger.warning(
                "[%s] postback to page %s did not change the row set — stopping pagination",
                self.key,
                target,
            )
            return False

        return True

    async def _current_page_number(self, pager) -> int:
        """The active page is rendered as plain text, not as a link."""
        text = clean_text(await pager.inner_text()) or ""
        linked = set()
        for link in await pager.query_selector_all("a"):
            value = clean_text(await link.inner_text())
            if value:
                linked.add(value)

        for token in text.split():
            if token.isdigit() and token not in linked:
                return int(token)
        return 1

    async def _first_row_signature(self, page) -> str:
        rows = await self._data_rows(page)
        for row in rows:
            text = clean_text(await row.inner_text())
            if text:
                return text
        return ""

    # --- Normalization (pure, testable without a browser) --------------------

    def build_stub(
        self,
        *,
        source_url: str,
        title: str,
        reference: str | None = None,
        buyer_text: str | None = None,
        publication_text: str | None = None,
        deadline_text: str | None = None,
        status_text: str | None = None,
        row_text: str | None = None,
    ) -> TenderStub:
        title = clean_text(title) or source_url
        reference = clean_text(reference)

        return TenderStub(
            external_id=self.external_id_from(source_url, reference, title),
            title=title,
            source_url=source_url,
            buyer_name=clean_text(buyer_text) or "משרד הביטחון",
            publication_date=parse_israeli_datetime(publication_text),
            # Wall-clock Israel time, and never rounded up to end of day.
            deadline=parse_israeli_datetime(deadline_text),
            listing_metadata={
                "reference_number": reference,
                "raw_status": clean_text(status_text),
                "normalized_status": parse_status(status_text),
                "raw_row": row_text,
                "portal": self.key,
            },
        )

    def external_id_from(
        self, source_url: str, reference: str | None = None, title: str | None = None
    ) -> str:
        """Dedup key, most stable source first.

        The BALAM reference column is the portal's own identifier and the
        number a buyer and a supplier both quote, so it wins over anything
        parsed out of prose or lifted from a URL that may be rewritten.
        """
        if reference:
            return reference

        from_title = extract_tender_number(title)
        if from_title:
            return from_title

        return self.external_id_from_url(source_url)

    def enrich(self, fetched: FetchedTender) -> FetchedTender:
        """Fill gaps from the detail page with cheap deterministic parsing.

        Runs before the LLM, so the model is not paid to re-derive a date the
        page states plainly.
        """
        stub = fetched.stub
        body = fetched.raw_text

        if stub.deadline is None:
            for label in ("מועד אחרון להגשה", "תאריך אחרון להגשה", "מועד אחרון", "מועד הגשה"):
                value = self._value_after(body, label)
                if value:
                    stub.deadline = parse_israeli_datetime(value)
                    if stub.deadline:
                        break

        if stub.publication_date is None:
            for label in ("תאריך פרסום", "תאריך הפרסום"):
                value = self._value_after(body, label)
                if value:
                    stub.publication_date = parse_israeli_datetime(value)
                    if stub.publication_date:
                        break

        amount, currency = parse_amount(self._value_after(body, "אומדן"))
        if amount is not None:
            stub.listing_metadata["estimated_value"] = amount
            stub.listing_metadata["currency"] = currency

        if stub.listing_metadata.get("normalized_status") is None:
            stub.listing_metadata["normalized_status"] = parse_status(body)

        return fetched

    @classmethod
    def _value_after(cls, text: str | None, label: str, *, window: int = 160) -> str | None:
        from .govil import GovIlTendersAgent

        return GovIlTendersAgent._value_after(text, label, window=window)


def verified_contract(**overrides) -> BalamContract:
    """Build a verified contract — for tests and for local overrides."""
    return replace(CONTRACT, verified=True, **overrides)
