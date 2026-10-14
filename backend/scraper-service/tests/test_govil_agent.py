"""Tests for the gov.il agent's normalization path.

These exercise everything between raw portal strings and a persisted stub:
Hebrew date and status parsing, dedup key selection, detail-page field
recovery, and the guard that stops the agent running on unverified selectors.

They do NOT prove the CSS selectors are right — no test can, short of the
live site. That is what `tools/probe_govil.py` is for. The split is
deliberate: the logic that would silently corrupt data is covered here, and
the part that fails loudly (selectors matching nothing) is covered by the
probe plus the guard below.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

import pytest

from app.agents.base import FetchedTender
from app.agents.govil import CONTRACT, GovIlTendersAgent, PortalContractUnverified
from app.agents.hebrew import ISRAEL_TZ
from app.config import Settings


def agent(**contract_overrides) -> GovIlTendersAgent:
    contract = replace(CONTRACT, verified=True, **contract_overrides)
    return GovIlTendersAgent(Settings(scrape_backoff_base_seconds=0.0), contract=contract)


# --- The unverified-contract guard -------------------------------------------


@pytest.mark.asyncio
async def test_agent_refuses_to_run_while_contract_is_unverified():
    """An unverified selector set returns zero tenders, which looks exactly
    like a quiet week. Failing loudly is the whole point."""
    unverified = GovIlTendersAgent(Settings(), contract=replace(CONTRACT, verified=False))

    with pytest.raises(PortalContractUnverified, match="probe_govil"):
        await unverified.discover()


@pytest.mark.asyncio
async def test_fetch_is_guarded_too():
    unverified = GovIlTendersAgent(Settings(), contract=replace(CONTRACT, verified=False))
    stub = unverified.build_stub(source_url="https://www.gov.il/x", title="מכרז")

    with pytest.raises(PortalContractUnverified):
        await unverified.fetch(stub)


def test_shipped_contract_is_unverified():
    """Guards against someone flipping the flag without running the probe."""
    assert CONTRACT.verified is False


# --- Stub normalization ------------------------------------------------------


def test_listing_card_becomes_a_normalized_stub():
    stub = agent().build_stub(
        source_url="https://www.gov.il/he/pages/tender-2026-117",
        title="‏מכרז פומבי מס' 117/2026 לאספקת מערכות קשר‎",
        buyer_text="  משרד הביטחון  ",
        publication_text="01/03/2026",
        deadline_text='מועד אחרון להגשה: 03/04/2026 בשעה 12:00',
        status_text="פתוח להגשה",
    )

    assert stub.title == "מכרז פומבי מס' 117/2026 לאספקת מערכות קשר"
    assert stub.buyer_name == "משרד הביטחון"
    assert stub.deadline == datetime(2026, 4, 3, 12, 0, tzinfo=ISRAEL_TZ)
    assert stub.listing_metadata["normalized_status"] == "open"


def test_deadline_uses_israeli_day_first_reading():
    """03/04 is 3 April. Read as 4 March the bid team sees a deadline a month
    early and deprioritizes a live tender."""
    stub = agent().build_stub(
        source_url="https://www.gov.il/he/pages/x",
        title="מכרז",
        deadline_text="03/04/2026 12:00",
    )
    assert stub.deadline.month == 4
    assert stub.deadline.day == 3


def test_deadline_carries_israel_timezone_not_naive_local():
    stub = agent().build_stub(
        source_url="https://www.gov.il/he/pages/x", title="מכרז", deadline_text="03/04/2026 12:00"
    )
    assert stub.deadline.tzinfo is not None
    assert stub.deadline.utcoffset().total_seconds() == 3 * 3600  # IDT in April


def test_date_only_deadline_is_not_rounded_up_to_end_of_day():
    stub = agent().build_stub(
        source_url="https://www.gov.il/he/pages/x", title="מכרז", deadline_text="03/04/2026"
    )
    assert stub.deadline.hour == 0


def test_missing_fields_stay_null():
    stub = agent().build_stub(source_url="https://www.gov.il/he/pages/x", title="מכרז ללא פרטים")

    assert stub.deadline is None
    assert stub.publication_date is None
    assert stub.buyer_name is None
    assert stub.listing_metadata["normalized_status"] is None


def test_raw_status_is_kept_alongside_the_normalized_one():
    """The portal's own wording stays available for the detail view, so a
    normalization mistake is visible rather than buried."""
    stub = agent().build_stub(
        source_url="https://www.gov.il/he/pages/x", title="מכרז", status_text="המכרז בוטל"
    )
    assert stub.listing_metadata["raw_status"] == "המכרז בוטל"
    assert stub.listing_metadata["normalized_status"] == "cancelled"


# --- Dedup key ---------------------------------------------------------------


def test_external_id_prefers_the_buyers_tender_number():
    """The reference survives a URL change and is what a BD team quotes."""
    assert (
        agent().external_id_from(
            "https://www.gov.il/he/pages/some-slug-that-may-change",
            "מכרז פומבי מס' 117/2026 לאספקת מערכות קשר",
        )
        == "117/2026"
    )


def test_external_id_falls_back_to_the_url_slug():
    assert (
        agent().external_id_from("https://www.gov.il/he/pages/tender-abc-123", "אספקת שירותים")
        == "tender-abc-123"
    )


def test_same_tender_under_two_urls_dedupes_to_one_id():
    """(source_id, external_id) is UNIQUE, so a stable key is what stops the
    same tender being alerted on twice."""
    a = agent()
    first = a.external_id_from("https://www.gov.il/he/pages/x1", "מכרז מס' 44/2026 לציוד")
    second = a.external_id_from("https://www.gov.il/he/pages/x2-updated", "מכרז מס' 44/2026 לציוד")
    assert first == second == "44/2026"


# --- Detail-page enrichment --------------------------------------------------

DETAIL_TEXT = """
משרד: משרד התחבורה והבטיחות בדרכים
תאריך פרסום: 12/02/2026
מועד אחרון להגשה: 20/03/2026 בשעה 14:00
אומדן: 3,750,000 ש"ח
סטטוס: פתוח
תיאור: אספקה והתקנה של מערכות בקרת תנועה.
"""


def test_enrich_recovers_fields_the_listing_card_omitted():
    a = agent()
    stub = a.build_stub(source_url="https://www.gov.il/he/pages/x", title="מכרז מס' 9/2026")
    fetched = a.enrich(FetchedTender(stub=stub, raw_text=DETAIL_TEXT))

    assert fetched.stub.deadline == datetime(2026, 3, 20, 14, 0, tzinfo=ISRAEL_TZ)
    assert fetched.stub.publication_date.date().isoformat() == "2026-02-12"
    assert fetched.stub.listing_metadata["estimated_value"] == 3_750_000.0
    assert fetched.stub.listing_metadata["currency"] == "ILS"


def test_enrich_does_not_overwrite_values_from_the_listing():
    """The card is the more reliable source for the fields it does carry;
    enrichment only fills gaps."""
    a = agent()
    stub = a.build_stub(
        source_url="https://www.gov.il/he/pages/x",
        title="מכרז",
        deadline_text="01/01/2027 09:00",
    )
    a.enrich(FetchedTender(stub=stub, raw_text=DETAIL_TEXT))

    assert stub.deadline == datetime(2027, 1, 1, 9, 0, tzinfo=ISRAEL_TZ)


def test_enrich_is_a_no_op_on_an_empty_page():
    a = agent()
    stub = a.build_stub(source_url="https://www.gov.il/he/pages/x", title="מכרז")
    a.enrich(FetchedTender(stub=stub, raw_text=""))

    assert stub.deadline is None
    assert stub.buyer_name is None


def test_field_window_stops_at_the_next_label():
    """Labels are not reliably marked up as label/value pairs, so the reader
    takes a bounded window. It must not swallow the following field."""
    value = GovIlTendersAgent._value_after(DETAIL_TEXT, "תאריך פרסום")
    assert value.startswith("12/02/2026")
    assert "מועד אחרון" not in value


def test_agent_is_registered_under_a_stable_key():
    from app.agents import available_keys

    assert "govil" in available_keys()
