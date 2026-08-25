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
from app.agents.mod_balam import CONTRACT, ModBalamAgent, verified_contract
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
    stub = agent().build_stub(
        source_url="https://www.online.mod.gov.il/Online2016/Pages/General/Balam/Detail.aspx?id=884",
        reference="  4-2026  ",
        title='‏אספקת מערכות קשר טקטיות‎',
        buyer_text="אגף הרכש",
        publication_text="12/02/2026",
        deadline_text="20/03/2026 14:00",
        status_text="פתוח",
    )

    assert stub.external_id == "4-2026"
    assert stub.title == "אספקת מערכות קשר טקטיות"
    assert stub.buyer_name == "אגף הרכש"
    assert stub.deadline == datetime(2026, 3, 20, 14, 0, tzinfo=ISRAEL_TZ)
    assert stub.listing_metadata["normalized_status"] == "open"


def test_buyer_defaults_to_the_ministry():
    """Every row on this portal is MOD procurement, so an empty buyer column
    is still a known buyer — not an unknown one."""
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


# --- WebForms pagination -----------------------------------------------------


class FakeElement:
    def __init__(self, text: str = "", attrs: dict | None = None, children: list | None = None):
        self._text = text
        self._attrs = attrs or {}
        self._children = children or []
        self.clicked = False

    async def inner_text(self):
        return self._text

    async def get_attribute(self, name):
        return self._attrs.get(name)

    async def query_selector(self, selector):
        results = await self.query_selector_all(selector)
        return results[0] if results else None

    async def query_selector_all(self, selector):
        if selector == "a":
            return [c for c in self._children if c._attrs.get("tag") == "a"]
        return self._children

    async def click(self):
        self.clicked = True


class FakePager(FakeElement):
    def __init__(self, current: int, links: list[str]):
        children = [FakeElement(text=t, attrs={"tag": "a"}) for t in links]
        # WebForms renders the active page as plain text among the links.
        super().__init__(text=" ".join([*links, str(current)]), children=children)


class FakePage:
    def __init__(self, pager: FakePager | None, *, row_changes: bool = True):
        self._pager = pager
        self._row_changes = row_changes
        self.waited = False

    async def query_selector(self, selector):
        return self._pager

    async def query_selector_all(self, selector):
        return [FakeElement(text="row-1")]

    async def wait_for_function(self, *args, **kwargs):
        self.waited = True
        if not self._row_changes:
            raise TimeoutError("row set unchanged")


@pytest.mark.asyncio
async def test_pagination_clicks_the_numbered_successor():
    pager = FakePager(current=2, links=["1", "3", "4", "..."])
    page = FakePage(pager)

    assert await agent()._go_to_next_page(page) is True
    assert [c for c in pager._children if c.clicked][0]._text == "3"


@pytest.mark.asyncio
async def test_pagination_does_not_follow_the_ellipsis():
    """A WebForms pager's '...' jumps a whole block of pages. Following it
    would skip rows without any error."""
    pager = FakePager(current=2, links=["1", "..."])
    assert await agent()._go_to_next_page(FakePage(pager)) is False


@pytest.mark.asyncio
async def test_pagination_stops_when_the_row_set_does_not_change():
    """The URL never changes on a postback, so an unchanged row set is the
    only signal that paging is done. Continuing would re-ingest page N."""
    pager = FakePager(current=2, links=["1", "3"])
    page = FakePage(pager, row_changes=False)

    assert await agent()._go_to_next_page(page) is False
    assert page.waited is True


@pytest.mark.asyncio
async def test_pagination_stops_with_no_pager():
    assert await agent()._go_to_next_page(FakePage(None)) is False


@pytest.mark.asyncio
async def test_current_page_is_the_number_that_is_not_a_link():
    pager = FakePager(current=3, links=["1", "2", "4"])
    assert await agent()._current_page_number(pager) == 3
