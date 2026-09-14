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
    sector = "defence"     # read by run_source when it registers the source

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


# --- the cost of a sweep is measured in requests, not tenders --------------

@pytest.mark.asyncio
async def test_one_embedding_request_covers_the_whole_batch(monkeypatch):
    """The free tier limits EmbedContentRequestsPerMinute.

    The provider has batched since it was written; the pipeline called it
    with one text at a time and nobody noticed, because while ingestion also
    paid for an LLM read the embedding request count was invisible next to
    it. At scale it is the only number that matters: at one request per
    tender a 6,000-notice sweep died after 213.
    """
    agent = _Agent([_stub(i) for i in range(250)])
    monkeypatch.setattr("app.pipeline.get_agents", lambda *a, **k: [agent])

    # dry_run=False on purpose: a dry run no longer embeds at all, so with
    # the default this test would count zero requests and pass for the
    # opposite of the reason it exists. The repository is a stub so nothing
    # reaches a real database.
    class _Repo:
        """Everything run_source touches on the live path, and nothing else."""

        def __init__(self):
            self.rows = []

        def upsert_source(self, row):
            return "source-id"

        def start_run(self, source_id):
            return "run-id"

        def finish_run(self, run_id, **kw):
            return None

        def ingest_fingerprints(self, source_id):
            return {}

        def upsert_tender(self, row):
            self.rows.append(row)
            return "id"

    pipeline = IngestionPipeline(
        settings=make_settings(extract_on_ingest=False, embed_batch_size=96,
                               dry_run=False),
        repository=_Repo(),
    )

    requests = []
    original = pipeline.embedder.embed

    async def _counting(texts, **kw):
        requests.append(len(texts))
        return await original(texts, **kw)

    monkeypatch.setattr(pipeline.embedder, "embed", _counting)
    (report,) = await pipeline.run(["fake"], limit=250)

    assert report.ingested == 250
    assert requests == [96, 96, 58], requests
    assert len(requests) == 3, f"250 tenders took {len(requests)} requests"


@pytest.mark.asyncio
async def test_the_final_partial_batch_is_not_dropped(monkeypatch):
    """The tail is the easiest thing in the world to lose silently: every
    counter agrees, and the missing tenders look exactly like tenders the
    portal never published."""
    agent = _Agent([_stub(i) for i in range(5)])
    monkeypatch.setattr("app.pipeline.get_agents", lambda *a, **k: [agent])

    pipeline = IngestionPipeline(
        settings=make_settings(extract_on_ingest=False, embed_batch_size=96)
    )
    (report,) = await pipeline.run(["fake"], limit=5)

    assert report.ingested == 5, "a batch smaller than the batch size vanished"


@pytest.mark.asyncio
async def test_a_blank_listing_does_not_cost_the_batch_its_vectors(monkeypatch):
    """One unembeddable listing among ninety-five good ones must not take
    them with it -- and must not shift the others' vectors onto the wrong
    rows either, which is what dropping a text without dropping its stub
    would do."""
    stubs = [_stub(0), TenderStub(external_id="blank", title="  ",
                                  source_url="https://example.invalid/n"),
             _stub(2)]
    agent = _Agent(stubs)
    monkeypatch.setattr("app.pipeline.get_agents", lambda *a, **k: [agent])

    pipeline = IngestionPipeline(settings=make_settings(extract_on_ingest=False))
    (report,) = await pipeline.run(["fake"], limit=3)

    assert report.ingested == 2
    assert report.failed == 1
    assert any("no text" in e for e in report.errors)


@pytest.mark.asyncio
async def test_a_dry_run_does_not_spend_the_embedding_budget(monkeypatch):
    """A dry run that costs money is not a dry run.

    Found by running one. `--dry-run --limit 40` wrote nothing to the
    database, exactly as promised, and still called the embedding API forty
    tenders' worth -- against a quota that was already exhausted, so the run
    reported `failed 40` for a reason that had nothing to do with tenders.

    A dry run exists to check the fetch and the filter. Neither needs a
    vector.
    """
    agent = _Agent([_stub(i) for i in range(30)])
    monkeypatch.setattr("app.pipeline.get_agents", lambda *a, **k: [agent])

    pipeline = IngestionPipeline(settings=make_settings(dry_run=True,
                                                       extract_on_ingest=False))
    called = []

    async def _boom(texts, **kw):
        called.append(len(texts))
        raise AssertionError("a dry run called the embedding API")

    monkeypatch.setattr(pipeline.embedder, "embed", _boom)
    (report,) = await pipeline.run(["fake"], limit=30)

    assert called == []
    assert report.ingested == 30, "a dry run should still report what it would have written"
    assert report.failed == 0
