# -*- coding: utf-8 -*-
"""Find a Tender (UK), tested against a real captured release package.

`tests/data/fts_release_package.json` is ten releases pulled from the live
API, saved verbatim. Nothing in it is invented, which matters more than
usual here: this session shipped two defects behind green tests that
asserted payloads I had assumed rather than ones the service sends.
"""
from __future__ import annotations

import json
import pathlib
from datetime import datetime, timezone

import pytest

from app.agents.find_a_tender import FindATenderAgent, cpv_codes, parse_ocds_datetime
from app.config import Settings

DATA = pathlib.Path(__file__).parent / "data" / "fts_release_package.json"
PACKAGE = json.loads(DATA.read_text(encoding="utf-8"))
RELEASES = PACKAGE["releases"]

SETTINGS = Settings(extraction_provider="gemini", gemini_api_key="k",
                    embedding_provider="hash")


def agent() -> FindATenderAgent:
    return FindATenderAgent(SETTINGS)


# --- the filter is most of the value ---------------------------------------

def test_most_of_the_feed_is_procurement_that_already_happened():
    """Measured on the captured page: one contract notice in ten.

    The API has no status parameter, so every release has to be judged here.
    Ingesting the feed as published would fill the corpus with awards a bid
    team cannot act on -- and they would look exactly like tenders.
    """
    tags = {}
    for release in RELEASES:
        tags["+".join(release.get("tag") or [])] = tags.get(
            "+".join(release.get("tag") or []), 0) + 1

    assert tags["award+contract"] == 7
    assert tags["tender"] == 1
    assert tags["planning"] == 1
    assert tags["awardUpdate+contractUpdate"] == 1


def test_an_award_notice_is_not_a_tender():
    award = next(r for r in RELEASES if "award" in (r.get("tag") or []))
    ok, why = agent().is_open_notice(award)
    assert ok is False
    assert "not a contract notice" in why


def test_a_planning_notice_is_not_a_tender():
    """A pipeline notice says a procurement is coming. There is nothing to
    bid on yet, and telling a customer otherwise wastes the one thing the
    product is selling them -- attention."""
    planning = next(r for r in RELEASES if r.get("tag") == ["planning"])
    ok, why = agent().is_open_notice(planning)
    assert ok is False
    assert "planning" in why


def test_the_one_real_notice_in_the_page_is_kept():
    notice = next(r for r in RELEASES if r.get("tag") == ["tender"])
    ok, why = agent().is_open_notice(
        notice, now=datetime(2026, 9, 14, tzinfo=timezone.utc)
    )
    assert ok is True, why


def test_a_tender_tag_with_a_complete_status_is_still_dropped():
    """`tag` and `tender.status` answer different questions, and a release
    can say `tender` while the procurement is over. Trusting either alone
    admits a different wrong set."""
    ok, why = agent().is_open_notice(
        {"tag": ["tender"], "tender": {"status": "complete"}}
    )
    assert ok is False
    assert why == "status complete"


def test_a_closed_deadline_is_dropped():
    ok, why = agent().is_open_notice(
        {"tag": ["tender"], "tender": {
            "status": "active",
            "tenderPeriod": {"endDate": "2020-01-01T12:00:00+00:00"}}}
    )
    assert ok is False
    assert why == "closed"


def test_a_missing_deadline_is_kept():
    """Unlike TED -- where a missing deadline meant an award notice already
    excluded -- here it is genuinely unknown, and a bid team would rather
    see it than not."""
    ok, why = agent().is_open_notice({"tag": ["tender"], "tender": {"status": "active"}})
    assert ok is True
    assert why == "no deadline published"


# --- dates, including the one that crashed a probe -------------------------

def test_an_offset_deadline_is_parsed():
    parsed = parse_ocds_datetime("2028-03-31T12:00:00+01:00")
    assert parsed is not None and parsed.tzinfo is not None


def test_a_naive_deadline_is_read_as_utc_rather_than_crashing():
    """Some notices publish a bare local datetime. Comparing that to an
    aware `now` raises TypeError, which ends the sweep -- found by a probe
    crashing on it, not by reading the schema. Wrong by at most an hour;
    an hour never decides whether a tender is worth looking at."""
    parsed = parse_ocds_datetime("2026-12-01T12:00:00")
    assert parsed == datetime(2026, 12, 1, 12, 0, tzinfo=timezone.utc)


def test_an_unparseable_date_is_none_not_an_exception():
    assert parse_ocds_datetime("next Tuesday") is None
    assert parse_ocds_datetime(None) is None
    assert parse_ocds_datetime("") is None


# --- mapping ---------------------------------------------------------------

def test_a_real_release_maps_to_a_stub():
    notice = next(r for r in RELEASES if r.get("tag") == ["tender"])
    stub = agent().build_stub(notice)

    assert stub is not None
    # The ocid, not the notice id: one procurement, one row, however
    # many releases the publisher issues about it.
    assert stub.external_id == notice["ocid"]
    assert stub.listing_metadata["notice_id"] == notice["id"]
    assert stub.title
    assert stub.source_url.startswith("https://www.find-tender.service.gov.uk/notice/")
    assert stub.listing_metadata["buyer_country"] == "GBR"
    assert stub.listing_metadata["portal"] == "uk-fts"


def test_the_stub_carries_enough_to_embed_without_reading_the_notice():
    """Listing-first ingestion pays for a vector, not a document. If the
    listing carries only a title the vector is thin, and this source is only
    worth adding because OCDS puts the description in the release."""
    notice = next(r for r in RELEASES if r.get("tag") == ["tender"])
    text = agent().build_stub(notice).listing_text()

    assert len(text) > 120, text
    assert notice["tender"]["title"][:30] in text


def test_both_cpv_lists_are_carried():
    """A defence lot inside a wider framework is routinely only in the
    additional classifications."""
    codes = cpv_codes({
        "classification": {"id": "35000000"},
        "additionalClassifications": [{"id": "35613000"}, {"id": "35000000"}],
    })
    assert codes == ["35000000", "35613000"], "a duplicate main code survived"


def test_a_release_with_no_id_is_refused():
    """Without a stable id the row could never be deduplicated or linked
    back, which makes it worse than absent."""
    assert agent().build_stub({"tender": {"title": "orphan"}}) is None


def test_a_release_with_no_title_is_refused():
    assert agent().build_stub({"id": "1", "ocid": "x", "tender": {}}) is None


def test_the_change_detector_moves_when_the_publisher_edits():
    """`raw_row` keys the skip index. Hashing the fields we happened to map
    would go stale the moment the mapping changed; OCDS gives a per-release
    date that moves on edit, which is a real fingerprint."""
    base = {"id": "086712-2026", "ocid": "ocds-x", "date": "2026-09-10T00:00:00Z",
            "tender": {"title": "a notice"}}
    # Same procurement, new release: the external_id must NOT move, and the
    # fingerprint must.
    edited = dict(base, id="086799-2026", date="2026-09-12T00:00:00Z")

    a, b = agent().build_stub(base), agent().build_stub(edited)
    assert a.external_id == b.external_id, "an amendment forked the procurement"
    assert a.listing_metadata["raw_row"] != b.listing_metadata["raw_row"]


# --- registration ----------------------------------------------------------

def test_the_agent_registers_itself():
    """Adding a portal must never require editing pipeline code (ADR-5)."""
    from app.agents import available_keys
    assert "uk-fts" in available_keys()


@pytest.mark.asyncio
async def test_fetch_needs_no_second_request():
    """OCDS puts the notice text in the release, so there is no detail page.
    A fetch that opened a browser here would cost what listing-first
    ingestion exists to avoid."""
    notice = next(r for r in RELEASES if r.get("tag") == ["tender"])
    stub = agent().build_stub(notice)
    fetched = await agent().fetch(stub)

    assert fetched.stub is stub
    assert stub.title in fetched.raw_text


def test_an_amendment_to_a_live_tender_is_kept():
    """`tenderUpdate` is a change to a procurement that is still running.

    Dropping it cost 63 of 264 candidate notices on a live 13-page walk, and
    the ones it cost were the tenders whose original release predated our
    window -- precisely the ones nobody else would have told us about.

    Safe to admit only because the row is keyed on the ocid: an update to
    something we already hold upserts onto it rather than competing with it.
    """
    ok, why = agent().is_open_notice({
        "tag": ["tenderUpdate"],
        "tender": {"status": "active",
                   "tenderPeriod": {"endDate": "2030-01-01T12:00:00+00:00"}},
    })
    assert ok is True, why


def test_an_award_update_is_still_dropped():
    """An update to a finished procurement is still a finished procurement."""
    ok, why = agent().is_open_notice({"tag": ["awardUpdate", "contractUpdate"],
                                      "tender": {"status": "complete"}})
    assert ok is False
