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

## Probe results — 2026-08-25

A live probe confirmed:

* `robots.txt` **allows** this path, for our agent and for `*`. No crawl-delay
  is declared, so the conservative defaults in `Settings` stand.
* Architecture is ASP.NET WebForms (`__VIEWSTATE`, `__doPostBack` present),
  as the URL suggested.
* `table[id*='gv'] tr` matches the listing (23 rows: header plus data).
* The column map, now recorded in `BalamContract`. Two findings from it were
  load-bearing: the deadline is written **time first** (`16:00 26/08/2026`),
  which silently dropped the submission time until `hebrew.py` was fixed to
  excise the date before looking for a clock; and titles carry a `המשך...`
  truncation marker that has to be stripped before storing or embedding.

**Pagination.** The pager carries no class, id or ARIA label — which is why
every container selector matched nothing — but its links are unambiguous:

    <a href="javascript:__doPostBack('ctl00$ContentPlaceHolder1$gvBalam','Page$2')">2</a>

Pages 2 through 9 were present, so the listing is at least nine pages deep.
An agent reading only page one would have taken roughly an eighth of the
tenders and reported success.

**Detail URLs.** The row's read-more control is not a postback after all:

    <a href="javascript:ShowDialog('/Online2016/.../BalamHeaderText.aspx?BalamId=1000957926');">

A real URL behind a dialog opener, confirmed by clicking it and landing on
that address. `BalamId` matched the reference number in column 1, so a URL
can also be built for rows that have no read-more link — which matters,
because that control only renders on titles long enough to be clipped.

## What this portal actually gives us

Worth being plain about, because it bounds what the rest of the pipeline can
do. The public listing carries: reference number, title, submission
deadline, publication date, and a contact phone (published by the portal,
deliberately not collected — see `col_contact`). `BalamHeaderText.aspx`
serves the tender's **full title** and nothing more — it is a read-more
dialog, not a specification page.

So there is no public document to run PEGS extraction over. Specifications,
threshold conditions and attachments sit behind the supplier login (the
`כניסה למערכת` control on the page), which this agent does not touch.

That still leaves real product value — aggregation, deadline alerts, and
semantic matching on the title — but a tender from this source will have a
thin `metadata` payload compared to one from a portal that publishes its
documents, and the dashboard should not imply otherwise.

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
import re
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


#: One definition of "what the first data row says", evaluated in the page for
#: both the before-snapshot and the after-comparison.
#:
#: It used to be two definitions. Python took the signature from `_data_rows()`
#: -- data rows only, run through `clean_text()` -- while the waiting predicate
#: walked every `tr` including the header and compared raw `innerText`. The two
#: could never be equal, so the predicate returned true on its first evaluation
#: whatever the postback had done. The guard that exists to notice a page which
#: did not turn reported "changed" every single time, and a listing read nine
#: times over reported nine pages.
#:
#: Sharing one expression removes the possibility of that drift rather than
#: fixing this instance of it: there is nothing left to keep in step.
_ROW_SIGNATURE_JS = """(selector) => {
  for (const row of document.querySelectorAll(selector)) {
    // A data row has at least two cells -- the same rule _read_listing_page
    // uses to skip header, pager and spacer rows.
    if (row.querySelectorAll('td').length < 2) continue;
    const text = (row.innerText || '').replace(/\s+/g, ' ').trim();
    if (text) return text;
  }
  return '';
}"""


@dataclass(frozen=True)
class BalamContract:
    """Everything about the BALAM list that must be observed, not reasoned.

    When the portal is redesigned, this is the only block that changes.
    """

    #: Verified against the live portal on 2026-08-25. Every value below was
    #: observed, not assumed: robots.txt permission, the architecture, the row
    #: selector, the column map, the pager control, and the detail URL — the
    #: last confirmed by clicking a row and landing on it.
    #:
    #: Set this back to False after any portal redesign, and re-run
    #: `python tools/probe_portal.py --url "<BALAM URL>" --detail-sample`.
    verified: bool = True

    #: Reset=1 clears server-side session filters. Always enter through it.
    listing_path: str = "/Online2016/Pages/General/Balam/BalamList.aspx?Reset=1"

    #: Confirmed by probe: `table[id*='gv'] tr` matched 23 rows (one header
    #: plus data). Header rows carry <th>, and `_read_listing_page` skips any
    #: row with fewer than two <td>, so no class-based exclusion is needed —
    #: the GridHeader/GridPager classes this previously guessed at do not
    #: exist on this page.
    table_selector: str = "table[id*='gv']"
    row_selector: str = "tr"

    #: Zero-based column indexes, taken from the probe's column map:
    #:   0  (empty — icon/select column)
    #:   1  1000957926              reference number
    #:   2  רכש דלקני מתכת...       title, with a "המשך..." truncation marker
    #:   3  (empty)
    #:   4  16:00 26/08/2026        submission deadline — TIME FIRST
    #:   5  06/08/2026              publication date
    #:   6  03-6977476              contact phone
    #: A wrong index here swaps two fields silently rather than failing, so
    #: re-run the probe after any portal change.
    col_reference: int = 1
    col_title: int = 2
    #: No buyer column: every row on this portal is MOD procurement.
    col_buyer: int | None = None
    col_deadline: int | None = 4
    col_publication_date: int | None = 5
    #: No status column on the listing. Status is inferred from the deadline
    #: by the pipeline, or read from the detail page.
    col_status: int | None = None
    #: Observed at index 6, and deliberately NOT extracted. The column holds
    #: a contact phone number: personal data under the Israeli privacy
    #: regulations, which nothing in this system reads, displays or matches
    #: on. Recorded here so the column map stays a complete description of
    #: the page; used by `_redacted_row` to exclude it from the raw row.
    col_contact: int | None = 6

    #: Confirmed by probe: the row's only link is the "המשך..." read-more
    #: control, rendered as
    #:   <a href="javascript:ShowDialog('/Online2016/.../BalamHeaderText.aspx?BalamId=1000957926');">
    #: Not a postback at all — a real URL wrapped in a dialog opener.
    detail_link_selector: str = "a[href*='ShowDialog'], a[href*='BalamId']"

    #: Fallback used when a row has no read-more link.
    #:
    #: This is load-bearing, not a nicety: the "המשך..." control only appears
    #: on titles long enough to be clipped. A tender with a short title has no
    #: link in its row at all, and without this template every such tender
    #: would be dropped for lack of a source URL — silently, and only for the
    #: tenders whose titles happen to be short.
    #:
    #: The probe confirmed `BalamId` equals the reference number in column 1
    #: for the sampled row. The link is still preferred wherever one exists,
    #: so this assumption only has to hold for unclipped rows.
    detail_url_template: str = "/Online2016/Pages/General/Balam/BalamHeaderText.aspx?BalamId={reference}"

    #: The GridView's WebForms control id, confirmed by probe. Every pager
    #: link posts back to this target with an argument of `Page$N`.
    grid_control_id: str = "ctl00$ContentPlaceHolder1$gvBalam"

    #: Pager links are matched by their postback argument, not by a container
    #: class. The probe found the pager carries no distinguishing class, id or
    #: ARIA label — every container selector tried matched zero elements — but
    #: the links themselves are unambiguous:
    #:   <a href="javascript:__doPostBack('...$gvBalam','Page$2')">2</a>
    #: Matching on the argument is also more precise than matching link text,
    #: which carries stray whitespace.
    pager_link_selector: str = "a[href*='Page$']"

    #: Truncation marker the portal appends to clipped titles in the listing.
    title_truncation_marker: str = "המשך..."

    detail_body_selector: str = "form#aspnetForm, body"
    attachment_selector: str = "a[href$='.pdf'], a[href*='Download'], a[href*='Attachment']"

    #: WebForms postbacks re-render the whole page; this is how long to wait
    #: for the row set to actually change before deciding paging is finished.
    postback_timeout_ms: int = 20_000


CONTRACT = BalamContract()


#: Matches both forms ASP.NET emits for a postback link. A LinkButton inside
#: a GridView frequently renders as `WebForm_DoPostBackWithOptions(new
#: WebForm_PostBackOptions("target", "argument", ...))` rather than a bare
#: `__doPostBack('target','argument')` — and a pattern that only knows the
#: bare form sees no links at all rather than failing, which is how the first
#: probe run reported "no row links" on a page that has them.
_POSTBACK = re.compile(
    r"""(?:__doPostBack|WebForm_PostBackOptions)\(\s*
        (?:new\s+WebForm_PostBackOptions\(\s*)?
        ['"](?P<target>[^'"]*)['"]\s*,\s*
        ['"](?P<argument>[^'"]*)['"]""",
    re.VERBOSE,
)


#: The portal's read-more control: a real URL wrapped in a dialog opener,
#: `javascript:ShowDialog('/Online2016/.../BalamHeaderText.aspx?BalamId=...')`.
_SHOW_DIALOG = re.compile(r"""ShowDialog\(\s*['"](?P<url>[^'"]+)['"]""")


def postback_argument(href: str | None) -> str | None:
    """Extract the argument from a WebForms postback href.

    Returns None for a plain URL or an unrecognized script call.
    """
    if not href:
        return None
    match = _POSTBACK.search(href)
    return match.group("argument") if match else None


def postback_target(href: str | None) -> str | None:
    """Extract the control id from a WebForms postback href."""
    if not href:
        return None
    match = _POSTBACK.search(href)
    return match.group("target") if match else None


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
        # Which GridView page we are on. Every run enters at page 1 through
        # ?Reset=1, so stepping this forward is exact — see _go_to_next_page
        # for why it is not read from the DOM.
        self._page_index = 1

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
        # Reset the walk: the agent instance may be reused across runs, and a
        # stale index would start page-two-onwards from the wrong number and
        # skip pages.
        self._page_index = 1

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

            reference = self._column(texts, self.contract.col_reference)

            link = await row.query_selector(self.contract.detail_link_selector)
            href = await link.get_attribute("href") if link else None

            # The read-more link exists only on clipped titles, so fall back
            # to building the URL from the reference number. Without this,
            # every short-titled tender would be dropped for lack of a link.
            source_url = self._detail_url(href) or self._detail_url_from_reference(reference)
            if source_url is None:
                # No link and no reference: nothing addressable to store. The
                # source URL is what makes an extraction auditable, so a row
                # without one is skipped rather than given a fabricated URL.
                logger.warning(
                    "[%s] skipping row with neither a detail link nor a reference number",
                    self.key,
                )
                continue

            stubs.append(
                self.build_stub(
                    source_url=source_url,
                    reference=reference,
                    title=self._column(texts, self.contract.col_title) or "",
                    buyer_text=self._column(texts, self.contract.col_buyer),
                    publication_text=self._column(texts, self.contract.col_publication_date),
                    deadline_text=self._column(texts, self.contract.col_deadline),
                    status_text=self._column(texts, self.contract.col_status),
                    row_text=self._redacted_row(texts),
                )
            )

        return stubs

    async def _data_rows(self, page) -> list:
        table = await page.query_selector(self.contract.table_selector)
        scope = table or page
        return await scope.query_selector_all(self.contract.row_selector)

    def _detail_url(self, href: str | None) -> str | None:
        """Resolve a row's link to an addressable URL.

        Three shapes, in order:

        1. A `ShowDialog('/path?BalamId=...')` wrapper — what this portal
           actually emits. The dialog opener hides a real URL, confirmed by
           the probe: clicking it lands on that exact address.
        2. A plain href.
        3. Any other `javascript:` call — no address, so None. A tender with
           no link back to its notice cannot be checked by a bid team, and a
           fabricated URL is worse than an absent one.
        """
        if not href:
            return None

        dialog = _SHOW_DIALOG.search(href)
        if dialog:
            return urljoin(self.base_url, dialog.group("url"))

        if href.strip().lower().startswith("javascript:"):
            return None

        return urljoin(self.base_url, href)

    def _detail_url_from_reference(self, reference: str | None) -> str | None:
        """Build the detail URL from the tender's reference number.

        Used for rows with no read-more link — see `detail_url_template`.
        """
        if not reference or not self.contract.detail_url_template:
            return None
        return urljoin(
            self.base_url, self.contract.detail_url_template.format(reference=reference.strip())
        )

    def _redacted_row(self, texts: list[str | None]) -> str:
        """The row as seen, minus the contact column.

        `raw_row` is a diagnostic: when the column map drifts, it is what
        shows you what the row actually said. But joining every cell would
        put the contact phone back into the database under a different key,
        which would make dropping `contact_phone` theatre rather than a
        removal. The one column carrying personal data is excluded here.
        """
        contact = self.contract.col_contact
        return " | ".join(
            text for index, text in enumerate(texts) if text and index != contact
        )

    @staticmethod
    def _column(texts: list[str | None], index: int | None) -> str | None:
        if index is None or index < 0 or index >= len(texts):
            return None
        return texts[index]

    # --- WebForms pagination -------------------------------------------------

    async def _go_to_next_page(self, page) -> bool:
        """Advance the GridView by one postback.

        The page number is tracked here rather than read from the DOM. A
        WebForms pager shows a moving window of links and renders the current
        page as plain text, so inferring "where am I" from the markup is
        guesswork that gets worse as the window slides. We always enter at
        page 1 through `?Reset=1` and step forward one at a time, so a counter
        is exact.

        The successor is matched on its postback *argument*, not its link
        text: `Page$3` is unambiguous, whereas link text carries stray
        whitespace, and a substring match would let `Page$3` also match
        `Page$30` once the listing passes 29 pages.

        The URL does not change across a postback, so there is nothing for a
        navigation wait to key on. Instead this snapshots the first data row
        and waits for it to change — the only reliable signal the postback
        landed.
        """
        target_page = self._page_index + 1
        next_link = await self._pager_link(page, target_page)

        if next_link is None:
            logger.info(
                "[%s] no pager link for page %d — end of listing", self.key, target_page
            )
            return False

        row_scope = f"{self.contract.table_selector} {self.contract.row_selector}"
        before = await page.evaluate(_ROW_SIGNATURE_JS, row_scope)

        await self.with_backoff(
            lambda: next_link.click(), description=f"postback to page {target_page}"
        )

        try:
            await page.wait_for_function(
                "([selector, previous, sig]) => {"
                "  const signature = new Function('return ' + sig)();"
                "  const now = signature(selector);"
                "  return now !== '' && now !== previous;"
                "}",
                arg=[row_scope, before, _ROW_SIGNATURE_JS],
                timeout=self.contract.postback_timeout_ms,
            )
        except Exception:  # noqa: BLE001
            # Rows did not change. Stop rather than re-ingesting the same page
            # under a new page number — duplicates would dedupe on
            # external_id, but the walk would never terminate on its own.
            logger.warning(
                "[%s] postback to page %d did not change the row set — stopping pagination",
                self.key,
                target_page,
            )
            return False

        self._page_index = target_page
        return True

    async def _pager_link(self, page, page_number: int):
        """Find the pager link that posts back with exactly `Page$<n>`."""
        wanted = f"Page${page_number}"

        for link in await page.query_selector_all(self.contract.pager_link_selector):
            href = await link.get_attribute("href") or ""
            if postback_argument(href) == wanted:
                return link
        return None

    async def _first_row_signature(self, page) -> str:
        for row in await self._data_rows(page):
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
        raw_title = clean_text(title)
        marker = self.contract.title_truncation_marker
        was_truncated = bool(raw_title and marker and raw_title.endswith(marker))

        title = self.clean_title(title) or source_url
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
                # The listing clipped this title; `enrich` recovers the full
                # text from the detail page.
                "title_truncated": was_truncated,
                "raw_row": row_text,
                "portal": self.key,
            },
        )

    def clean_title(self, title: str | None) -> str | None:
        """Strip the portal's truncation marker from a listing title.

        The listing clips long titles and appends "המשך..." as a read-more
        affordance. Left in place it becomes part of the stored title, shows
        up in the dashboard, and gets embedded — polluting the vector with a
        token every truncated tender shares.
        """
        cleaned = clean_text(title)
        if not cleaned:
            return None

        marker = self.contract.title_truncation_marker
        if marker and cleaned.endswith(marker):
            cleaned = cleaned[: -len(marker)].strip()

        return cleaned or None

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

        # BalamHeaderText.aspx serves the tender's full title — that is what
        # the read-more control exists to show. Recovering it matters beyond
        # display: the title is most of what gets embedded for semantic
        # matching, and a clipped one loses exactly the trailing detail
        # (model numbers, quantities, variants) that distinguishes tenders.
        if stub.listing_metadata.get("title_truncated"):
            full_title = clean_text(body)
            if full_title and len(full_title) > len(stub.title):
                stub.listing_metadata["listing_title"] = stub.title
                stub.title = full_title
                stub.listing_metadata["title_truncated"] = False

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
