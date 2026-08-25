"""Tests for the MOD BALAM agent's normalization and row handling.

These cover what would corrupt data silently: positional column reads, the
dedup key, Israeli date/timezone handling, and the refusal to store a tender
without a real source URL.

Pagination is exercised through a fake page object rather than a browser —
the WebForms postback logic (locating the numbered successor, waiting for the
row set to actually change, stopping instead of re-ingesting) is where the
bugs live, and none of it needs Chromium to test.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.agents.base import FetchedTender
from app.agents.govil import PortalContractUnverified
from app.agents.hebrew import ISRAEL_TZ
from app.agents.mod_balam import (
    CONTRACT,
    ModBalamAgent,
    postback_argument,
    postback_target,
    verified_contract,
)
from app.config import Settings


def agent(**overrides) -> ModBalamAgent:
    return ModBalamAgent(
        Settings(scrape_backoff_base_seconds=0.0, scrape_max_retries=1),
        contract=verified_contract(**overrides),
    )


# --- Guard -------------------------------------------------------------------


def test_shipped_contract_is_unverified():
    assert CONTRACT.verified is False


@pytest.mark.asyncio
async def test_discover_refuses_while_contract_is_unverified():
    unverified = ModBalamAgent(Settings())
    with pytest.raises(PortalContractUnverified, match="probe_portal"):
        await unverified.discover()


@pytest.mark.asyncio
async def test_fetch_refuses_while_contract_is_unverified():
    unverified = ModBalamAgent(Settings())
    stub = agent().build_stub(source_url="https://x/y", title="מכרז")
    with pytest.raises(PortalContractUnverified):
        await unverified.fetch(stub)


def test_agent_is_registered():
    from app.agents import available_keys

    assert "mod-balam" in available_keys()


# --- Row normalization -------------------------------------------------------


def test_row_becomes_a_normalized_stub():
    """Values taken from a live probe row of the BALAM listing."""
    stub = agent().build_stub(
        source_url="https://www.online.mod.gov.il/Online2016/Pages/General/Balam/Detail.aspx?id=884",
        reference="  1000957926  ",
        title="רכש דלקני מתכת 20 ליטר ריקים המשך...",
        publication_text="06/08/2026",
        deadline_text="16:00 26/08/2026",
        contact_text="03-6977476",
    )

    assert stub.external_id == "1000957926"
    assert stub.title == "רכש דלקני מתכת 20 ליטר ריקים"
    assert stub.buyer_name == "משרד הביטחון"
    assert stub.deadline == datetime(2026, 8, 26, 16, 0, tzinfo=ISRAEL_TZ)
    assert stub.publication_date.date().isoformat() == "2026-08-06"
    assert stub.listing_metadata["contact_phone"] == "03-6977476"


def test_portal_deadline_format_keeps_the_submission_time():
    """The listing writes the deadline time-first. Dropping the time makes
    the deadline midnight — sixteen hours early, and still a valid value."""
    stub = agent().build_stub(
        source_url="https://x/y", title="מכרז", deadline_text="16:00 26/08/2026"
    )
    assert (stub.deadline.hour, stub.deadline.minute) == (16, 0)


def test_truncation_marker_is_stripped_from_the_title():
    """The listing clips long titles with 'המשך...'. Left in, it is stored,
    shown in the dashboard, and embedded — a token every truncated tender
    shares, which pulls their vectors together."""
    stub = agent().build_stub(
        source_url="https://x/y", title="רכש דלקני מתכת 20 ליטר ריקים המשך..."
    )
    assert stub.title == "רכש דלקני מתכת 20 ליטר ריקים"


def test_untruncated_titles_are_untouched():
    stub = agent().build_stub(source_url="https://x/y", title="אספקת מערכות קשר טקטיות")
    assert stub.title == "אספקת מערכות קשר טקטיות"


def test_bidi_marks_are_stripped_from_titles():
    stub = agent().build_stub(source_url="https://x/y", title="\u200fאספקת מערכות קשר\u200e")
    assert stub.title == "אספקת מערכות קשר"


def test_buyer_defaults_to_the_ministry():
    """The listing has no buyer column — every row is MOD procurement, so an
    absent buyer is a known buyer, not an unknown one."""
    stub = agent().build_stub(source_url="https://x/y", title="מכרז")
    assert stub.buyer_name == "משרד הביטחון"


def test_deadline_is_day_first_and_timezone_aware():
    stub = agent().build_stub(
        source_url="https://x/y", title="מכרז", deadline_text="03/04/2026 12:00"
    )
    assert (stub.deadline.day, stub.deadline.month) == (3, 4)
    assert stub.deadline.utcoffset().total_seconds() == 3 * 3600  # IDT


def test_missing_deadline_stays_null():
    stub = agent().build_stub(source_url="https://x/y", title="מכרז", deadline_text="טרם נקבע")
    assert stub.deadline is None


# --- Dedup key ---------------------------------------------------------------


def test_reference_column_wins_over_title_and_url():
    """The portal's own reference is the identifier both sides quote, and it
    survives a URL rewrite."""
    key = agent().external_id_from(
        "https://www.online.mod.gov.il/x/Detail.aspx?id=99",
        "4-2026",
        "מכרז מס' 77/2026",
    )
    assert key == "4-2026"


def test_falls_back_to_the_title_reference():
    key = agent().external_id_from("https://x/Detail.aspx?id=99", None, "מכרז מס' 77/2026")
    assert key == "77/2026"


def test_falls_back_to_the_url_when_nothing_else_exists():
    key = agent().external_id_from("https://x/tender-abc", None, "אספקת שירותים")
    assert key == "tender-abc"


# --- Detail links ------------------------------------------------------------


def test_javascript_postback_links_are_rejected():
    """A tender with no addressable URL cannot be checked against its source,
    and the source link is what makes the extraction trustworthy. Skipping
    beats storing a fabricated URL."""
    assert agent()._detail_url("javascript:__doPostBack('gv','Select$3')") is None
    assert agent()._detail_url("JavaScript:void(0)") is None
    assert agent()._detail_url(None) is None


def test_relative_hrefs_resolve_against_the_portal():
    assert (
        agent()._detail_url("/Online2016/Pages/General/Balam/Detail.aspx?id=5")
        == "https://www.online.mod.gov.il/Online2016/Pages/General/Balam/Detail.aspx?id=5"
    )


# --- Positional columns ------------------------------------------------------


def test_column_reader_tolerates_short_rows():
    """WebForms tables render spacer and colspan rows. Reading past the end
    must return None, not raise mid-page and lose the rest of the results."""
    texts = ["4-2026", "כותרת"]
    assert ModBalamAgent._column(texts, 0) == "4-2026"
    assert ModBalamAgent._column(texts, 5) is None
    assert ModBalamAgent._column(texts, None) is None


def test_column_indexes_are_configurable_per_portal_layout():
    """Column order is observed, not assumed, so it lives in the contract."""
    stub = agent(col_reference=2, col_title=0).build_stub(
        source_url="https://x/y",
        reference="REF-9",
        title="כותרת",
    )
    assert stub.external_id == "REF-9"


def test_contract_matches_the_probed_column_map():
    """Guards the observed layout: a silent index shift swaps two fields
    rather than raising, so the mapping is asserted rather than trusted."""
    assert (CONTRACT.col_reference, CONTRACT.col_title) == (1, 2)
    assert (CONTRACT.col_deadline, CONTRACT.col_publication_date) == (4, 5)
    # Neither column exists on this listing.
    assert CONTRACT.col_buyer is None
    assert CONTRACT.col_status is None


# --- Detail enrichment -------------------------------------------------------

DETAIL_TEXT = """
תאריך פרסום: 12/02/2026
מועד אחרון להגשה: 20/03/2026 בשעה 14:00
אומדן: 3,750,000 ש"ח
סטטוס: פתוח
"""


def test_enrich_fills_gaps_from_the_detail_page():
    a = agent()
    stub = a.build_stub(source_url="https://x/y", title="מכרז", reference="4-2026")
    a.enrich(FetchedTender(stub=stub, raw_text=DETAIL_TEXT))

    assert stub.deadline == datetime(2026, 3, 20, 14, 0, tzinfo=ISRAEL_TZ)
    assert stub.listing_metadata["estimated_value"] == 3_750_000.0
    assert stub.listing_metadata["currency"] == "ILS"


def test_enrich_does_not_overwrite_the_listing_value():
    a = agent()
    stub = a.build_stub(
        source_url="https://x/y", title="מכרז", deadline_text="01/01/2027 09:00"
    )
    a.enrich(FetchedTender(stub=stub, raw_text=DETAIL_TEXT))
    assert stub.deadline.year == 2027


# --- Postback parsing --------------------------------------------------------


def test_postback_argument_parses_the_bare_form():
    href = "javascript:__doPostBack('ctl00$ContentPlaceHolder1$gvBalam','Page$2')"
    assert postback_argument(href) == "Page$2"
    assert postback_target(href) == "ctl00$ContentPlaceHolder1$gvBalam"


def test_postback_argument_parses_the_options_wrapper():
    """A GridView LinkButton often renders as WebForm_DoPostBackWithOptions
    rather than a bare __doPostBack. A pattern that only knows the bare form
    reports 'no links' on a page full of them — which is exactly what the
    first probe run did."""
    href = (
        'javascript:WebForm_DoPostBackWithOptions(new WebForm_PostBackOptions('
        '"ctl00$ContentPlaceHolder1$gvBalam", "Select$3", true, "", "", false, true))'
    )
    assert postback_argument(href) == "Select$3"
    assert postback_target(href) == "ctl00$ContentPlaceHolder1$gvBalam"


def test_postback_argument_is_none_for_a_plain_url():
    assert postback_argument("/Online2016/Pages/Detail.aspx?id=5") is None
    assert postback_argument("javascript:void(0)") is None
    assert postback_argument(None) is None


# --- WebForms pagination -----------------------------------------------------


class FakeLink:
    def __init__(self, href: str):
        self._href = href
        self.clicked = False

    async def get_attribute(self, name):
        return self._href if name == "href" else None

    async def inner_text(self):
        return ""

    async def click(self):
        self.clicked = True


def pager_link(page_number: int) -> FakeLink:
    return FakeLink(
        f"javascript:__doPostBack('ctl00$ContentPlaceHolder1$gvBalam','Page${page_number}')"
    )


class FakeRow:
    def __init__(self, text: str):
        self._text = text

    async def inner_text(self):
        return self._text


class FakePage:
    """Stands in for a Playwright page. Only the calls the pager makes."""

    def __init__(self, pager_pages: list[int], *, row_changes: bool = True, rows: str = "row-1"):
        self._links = [pager_link(n) for n in pager_pages]
        self._row_changes = row_changes
        self._rows = rows
        self.waited = False

    async def query_selector(self, selector):
        return self

    async def query_selector_all(self, selector):
        if "Page$" in selector:
            return self._links
        return [FakeRow(self._rows)]

    async def wait_for_function(self, *args, **kwargs):
        self.waited = True
        if not self._row_changes:
            raise TimeoutError("row set unchanged")

    def clicked_hrefs(self):
        return [link._href for link in self._links if link.clicked]


@pytest.mark.asyncio
async def test_pagination_clicks_the_next_numbered_page():
    a = agent()
    page = FakePage([2, 3, 4, 5, 6, 7, 8, 9])

    assert await a._go_to_next_page(page) is True
    assert "Page$2" in page.clicked_hrefs()[0]
    assert a._page_index == 2


@pytest.mark.asyncio
async def test_pagination_walks_forward_one_page_at_a_time():
    """WebForms cannot jump to page N: the URL never changes and each page is
    a postback from the one before it."""
    a = agent()
    visited = []

    for _ in range(4):
        page = FakePage([2, 3, 4, 5, 6, 7, 8, 9])
        assert await a._go_to_next_page(page) is True
        visited.append(page.clicked_hrefs()[0])

    assert [postback_argument(h) for h in visited] == ["Page$2", "Page$3", "Page$4", "Page$5"]
    assert a._page_index == 5


@pytest.mark.asyncio
async def test_page_index_does_not_advance_when_the_postback_fails():
    """Otherwise the walk would skip a page and never notice."""
    a = agent()
    page = FakePage([2, 3], row_changes=False)

    assert await a._go_to_next_page(page) is False
    assert a._page_index == 1


@pytest.mark.asyncio
async def test_pagination_stops_at_the_last_page():
    a = agent()
    a._page_index = 9
    page = FakePage([2, 3, 4, 5, 6, 7, 8, 9])

    assert await a._go_to_next_page(page) is False


@pytest.mark.asyncio
async def test_page_argument_matches_exactly_not_by_prefix():
    """A substring match would let Page$3 also select Page$30 once the
    listing passes 29 pages, silently skipping 27 pages of tenders."""
    a = agent()
    a._page_index = 2
    page = FakePage([30, 31, 3])

    assert await a._go_to_next_page(page) is True
    assert postback_argument(page.clicked_hrefs()[0]) == "Page$3"


@pytest.mark.asyncio
async def test_pagination_stops_when_no_pager_exists():
    assert await agent()._go_to_next_page(FakePage([])) is False


@pytest.mark.asyncio
async def test_contract_records_the_probed_grid_control_id():
    assert CONTRACT.grid_control_id == "ctl00$ContentPlaceHolder1$gvBalam"
