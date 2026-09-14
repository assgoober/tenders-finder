# -*- coding: utf-8 -*-
"""Listing-first ingestion (migration 0019).

A tender is embedded from what the buyer published, and the expensive read is
deferred until somebody's profile actually matches it. These tests pin the
three things that make that safe rather than merely cheap:

  * ingestion in listing mode makes NO extraction call at all;
  * a listing with no words is refused rather than stored unmatchable;
  * the extraction path still works, unchanged, when asked for.
"""
from __future__ import annotations

import pytest

from app.agents.base import FetchedTender, TenderStub
from app.config import Settings
from app.pipeline import IngestionPipeline


def make_settings(**kw) -> Settings:
    base = dict(
        extraction_provider="gemini",
        gemini_api_key="k",
        embedding_provider="hash",
        dry_run=True,
    )
    base.update(kw)
    return Settings(**base)


class _Agent:
    key = "fake"
    display_name = "fake"
    base_url = "https://example.invalid"
    country = "IL"

    def __init__(self, stubs):
        self._stubs = stubs
        self.fetches = 0

    async def discover(self, limit=25):
        return self._stubs

    async def fetch(self, stub):
        self.fetches += 1
        return FetchedTender(stub=stub, raw_text="the whole document")


def _stub(i: int, **kw) -> TenderStub:
    meta = {"description": f"supply of {i} unmanned aerial vehicles",
            "cpv_codes": [35613000]}
    meta.update(kw.pop("listing_metadata", {}))
    return TenderStub(
        external_id=f"t{i}",
        title=f"notice {i}",
        source_url="https://example.invalid/n",
        buyer_name="Ministry",
        listing_metadata=meta,
        **kw,
    )


# --- the point of the whole change ----------------------------------------

@pytest.mark.asyncio
async def test_listing_mode_never_calls_the_extractor(monkeypatch):
    """The saving is not "fewer" model calls. It is none.

    If ingestion still extracted occasionally this would all be theatre: the
    quota is a daily cap, so one call per tender and 0.9 calls per tender hit
    the same wall a day apart.
    """
    agent = _Agent([_stub(i) for i in range(5)])
    monkeypatch.setattr("app.pipeline.get_agents", lambda *a, **k: [agent])

    pipeline = IngestionPipeline(settings=make_settings(extract_on_ingest=False))

    called = []
    async def _boom(*a, **k):
        called.append(1)
        raise AssertionError("extraction was called during a listing-mode sweep")
    monkeypatch.setattr(pipeline.extractor, "extract", _boom)

    (report,) = await pipeline.run(["fake"], limit=5)

    assert called == []
    assert report.ingested == 5
    assert agent.fetches == 0, "listing mode must not fetch detail pages either"


@pytest.mark.asyncio
async def test_extraction_mode_still_fetches_and_extracts(monkeypatch):
    """The old path is a setting, not a deletion. A customer who wants every
    tender read on arrival can still have it."""
    agent = _Agent([_stub(i) for i in range(3)])
    monkeypatch.setattr("app.pipeline.get_agents", lambda *a, **k: [agent])

    pipeline = IngestionPipeline(settings=make_settings(extract_on_ingest=True))

    seen = []
    async def _record(fetched, source_id, report):
        seen.append(fetched.stub.external_id)
        report.ingested += 1
    monkeypatch.setattr(pipeline, "_ingest_one", _record)

    (report,) = await pipeline.run(["fake"], limit=3)

    assert seen == ["t0", "t1", "t2"]
    assert agent.fetches == 3


# --- what a listing has to carry to be worth storing ----------------------

@pytest.mark.asyncio
async def test_a_listing_with_no_text_is_refused_not_stored(monkeypatch):
    """An unembedded row is invisible to matching.

    Storing it would put a tender in the table that no query will ever
    return -- present in every count, absent from every result. Counting it
    as failed is the honest outcome.
    """
    blank = TenderStub(external_id="empty", title="   ",
                       source_url="https://example.invalid/n")
    agent = _Agent([blank])
    monkeypatch.setattr("app.pipeline.get_agents", lambda *a, **k: [agent])

    pipeline = IngestionPipeline(settings=make_settings(extract_on_ingest=False))
    (report,) = await pipeline.run(["fake"], limit=1)

    assert report.ingested == 0
    assert report.failed == 1
    assert any("no text" in e for e in report.errors)


def test_listing_text_prefers_the_buyers_own_words():
    """Title, description, buyer, CPV -- and no duplicate of the title."""
    stub = _stub(7, listing_metadata={"listing_title": "notice 7"})
    text = stub.listing_text()

    assert "notice 7" in text
    assert "unmanned aerial vehicles" in text
    assert "Ministry" in text
    assert "35613000" in text, "CPV codes carry meaning across languages"
    assert text.count("notice 7") == 1, "listing_title duplicated the title"


def test_a_thin_listing_is_still_embeddable():
    """The Israeli MoD list publishes a short title and nothing else.

    Listing-first does not make that portal worse -- it never had a document
    to extract. This pins that a title-only source still produces text rather
    than falling through to the refusal above.
    """
    stub = TenderStub(external_id="b1", title="spare parts for comms systems",
                      source_url="u", buyer_name="MoD")
    assert stub.listing_text() == "spare parts for comms systems\nMoD"


# --- the queue that decides where the budget goes -------------------------

@pytest.mark.asyncio
async def test_extract_matched_reads_the_queue_not_the_whole_table(monkeypatch):
    """Extraction is spent on evidence of demand, never on inventory."""
    calls = {}

    class _Repo:
        def tenders_awaiting_extraction(self, limit):
            calls["limit"] = limit
            return []

    pipeline = IngestionPipeline(settings=make_settings(extract_matched_limit=17),
                                 repository=_Repo())
    done = await pipeline.extract_matched()

    assert done == 0
    assert calls["limit"] == 17


@pytest.mark.asyncio
async def test_an_unreadable_queue_is_not_an_outage(monkeypatch):
    """If the queue cannot be read the sweep still finished. Returning 0 and
    logging beats raising into a run that already did its work."""
    class _Repo:
        def tenders_awaiting_extraction(self, limit):
            raise RuntimeError("PostgREST said no")

    pipeline = IngestionPipeline(settings=make_settings(), repository=_Repo())
    assert await pipeline.extract_matched() == 0
