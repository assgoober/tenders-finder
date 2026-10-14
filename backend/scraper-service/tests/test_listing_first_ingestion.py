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
from app.pipeline import IngestionPipeline, IngestReport


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


# --- the four hours of grinding -------------------------------------------

@pytest.mark.asyncio
async def test_a_failed_batch_is_let_go_of(monkeypatch):
    """The batch cleared only on success.

    So a failed flush left all 96 stubs in place, the next stub flushed 97,
    failed, then 98 -- one doomed embedding call per remaining tender, each
    burning its full retry budget. Observed in production: 225 failures at
    one a minute for four hours, which is also what spent the day's quota.
    """
    agent = _Agent([_stub(i) for i in range(12)])
    monkeypatch.setattr("app.pipeline.get_agents", lambda *a, **k: [agent])

    class _Repo:
        def upsert_source(self, row): return "s"
        def start_run(self, s): return "r"
        def finish_run(self, r, **kw): return None
        def ingest_fingerprints(self, s): return {}
        def upsert_tender(self, row): return "id"

    pipeline = IngestionPipeline(
        settings=make_settings(extract_on_ingest=False, embed_batch_size=4,
                               dry_run=False),
        repository=_Repo(),
    )

    sizes = []

    async def _always_fails(texts, **kw):
        sizes.append(len(texts))
        raise RuntimeError("provider said no")

    monkeypatch.setattr(pipeline.embedder, "embed", _always_fails)
    await pipeline.run(["fake"], limit=12)

    assert sizes, "the batch never flushed at all"
    assert max(sizes) == 4, f"the batch grew past its size: {sizes}"
    assert len(sizes) == 3, f"12 stubs at 4 per batch is 3 calls, got {len(sizes)}"


@pytest.mark.asyncio
async def test_a_daily_embedding_cap_stops_the_run(monkeypatch):
    """A rate limit is about this minute; a daily cap is about this run.

    Without the distinction the cap arrived as an ordinary error, the
    per-tender handler counted one failed tender, and the loop went on to
    fail every remaining tender for the same reason -- with `partial` false
    and the run reporting itself as finished.
    """
    from app.nlp.embeddings import EmbeddingQuotaExhausted

    agent = _Agent([_stub(i) for i in range(500)])
    monkeypatch.setattr("app.pipeline.get_agents", lambda *a, **k: [agent])

    class _Repo:
        def upsert_source(self, row): return "s"
        def start_run(self, s): return "r"
        def finish_run(self, r, **kw): return None
        def ingest_fingerprints(self, s): return {}
        def upsert_tender(self, row): return "id"

    pipeline = IngestionPipeline(
        settings=make_settings(extract_on_ingest=False, embed_batch_size=10,
                               dry_run=False),
        repository=_Repo(),
    )

    calls = []

    async def _cap(texts, **kw):
        calls.append(len(texts))
        raise EmbeddingQuotaExhausted("the daily embedding budget is spent")

    monkeypatch.setattr(pipeline.embedder, "embed", _cap)
    (report,) = await pipeline.run(["fake"], limit=500)

    assert len(calls) == 1, f"kept going after the day's budget was gone: {len(calls)} calls"
    assert report.partial is True, "a run that stopped early reported itself complete"
    assert any("aborted" in e for e in report.errors)


def test_the_daily_cap_is_recognised_from_the_real_body():
    """The body a live sweep received at 14:00, pasted rather than imagined."""
    import asyncio
    from app.nlp.embeddings import EmbeddingQuotaExhausted, GeminiEmbeddingProvider

    provider = GeminiEmbeddingProvider.__new__(GeminiEmbeddingProvider)
    provider.model_name = "gemini-embedding-001"
    provider.dimension = 1024
    provider.RATE_LIMIT_WAIT_SECONDS = 0.001

    REAL = ("429 RESOURCE_EXHAUSTED. Quota exceeded for metric: "
            "generativelanguage.googleapis.com/embed_content_free_tier_requests, "
            "limit: 1000, model: gemini-embedding-1.0. Please retry in 42.08s. "
            "quotaId: EmbedContentRequestsPerDayPerUserPerProjectPerModel-FreeTier")

    class _Models:
        async def embed_content(self, **kw):
            raise RuntimeError(REAL)

    class _Aio:
        models = _Models()

    class _Client:
        aio = _Aio()

    provider._client = _Client()

    class _Types:
        @staticmethod
        def EmbedContentConfig(**kw):
            return kw

    with pytest.raises(EmbeddingQuotaExhausted):
        asyncio.run(provider._embed_batch_with_retry(["a"], "RETRIEVAL_DOCUMENT", _Types))


def test_an_aborted_run_reports_what_it_never_reached():
    """`found 2220, ingested 0, skipped 0, failed 0` is four numbers that do
    not add up to anything.

    That is what a real UK Find a Tender run printed when the embedding
    budget was already spent by the source before it: 2,220 open notices
    discovered, and the very first batch aborted the run. Nothing failed --
    nothing was tried. But a reader scanning those four numbers sees a
    source with no problems.
    """
    from app.pipeline import IngestReport

    report = IngestReport(source_key="uk-fts")
    report.found = 2220
    attempted = report.ingested + report.skipped + report.failed + report.needs_review
    report.unattempted = max(0, report.found - attempted)

    assert report.unattempted == 2220
    assert (report.ingested + report.skipped + report.failed
            + report.needs_review + report.unattempted) == report.found


def test_a_complete_run_has_nothing_unattempted():
    from app.pipeline import IngestReport

    report = IngestReport(source_key="ted-eu")
    report.found, report.ingested, report.skipped = 100, 60, 40
    attempted = report.ingested + report.skipped + report.failed + report.needs_review
    assert max(0, report.found - attempted) == 0


# --- the queue is actually worked, and stops when the money does ----------

@pytest.mark.asyncio
async def test_an_exhausted_embedding_budget_stops_extraction():
    """Each extraction re-embeds its tender. When the embedding quota is
    gone, carrying on means paying for a model call per remaining tender
    and discarding every result."""
    from app.nlp.embeddings import EmbeddingQuotaExhausted

    attempts = []

    class _Repo:
        def tenders_awaiting_extraction(self, limit):
            return [{"external_id": f"t{i}", "source_url": "https://x/t", "title": "t"}
                    for i in range(5)]

    pipeline = IngestionPipeline(settings=make_settings(), repository=_Repo())

    class _Agent:
        key = "x"

        async def fetch(self, stub):
            attempts.append(stub.external_id)
            raise EmbeddingQuotaExhausted("daily embedding quota spent")

    pipeline._agent_for_source = lambda item: _Agent()
    assert await pipeline.extract_matched() == 0
    assert attempts == ["t0"], "extraction kept spending after the budget ran out"


@pytest.mark.asyncio
async def test_the_scheduled_sweep_extracts_before_it_harvests(monkeypatch):
    """`extract_matched` existed and nothing called it. Profiles go first of
    all: the harvest spends the embedding budget, and a customer's profile
    embedded after it waited night after night."""
    import logging
    from types import SimpleNamespace

    import app.agents
    import app.pipeline as pipeline_module
    import run_sweep

    order = []

    class _FakePipeline:
        def __init__(self, settings):
            self.settings = settings

        async def embed_pending_profiles(self):
            order.append("profiles")
            return 1

        async def extract_matched(self):
            order.append("extract")
            return 3

        async def run_source(self, agent, limit=25):
            order.append(f"harvest:{agent.key}")
            return IngestReport(source_key=agent.key, found=1, ingested=1)

        async def finish_sweep(self, reports, **kwargs):
            return True

    monkeypatch.setattr(pipeline_module, "IngestionPipeline", _FakePipeline)
    monkeypatch.setattr(app.agents, "get_agent",
                        lambda key, settings: SimpleNamespace(key=key, display_name=key))
    monkeypatch.setattr(run_sweep, "count_tenders", lambda settings, since: (10, 1))

    args = SimpleNamespace(source=["mod-balam", "ted-eu"], limit=5, dry_run=False)
    assert await run_sweep.run(args, logging.getLogger("t")) == 0
    assert order == ["profiles", "extract", "harvest:mod-balam", "harvest:ted-eu"]


@pytest.mark.asyncio
async def test_a_dry_run_sweep_spends_nothing_on_extraction(monkeypatch):
    import logging
    from types import SimpleNamespace

    import app.agents
    import app.pipeline as pipeline_module
    import run_sweep

    called = []

    class _FakePipeline:
        def __init__(self, settings):
            pass

        async def embed_pending_profiles(self):
            return 0

        async def extract_matched(self):
            called.append(True)
            return 0

        async def run_source(self, agent, limit=25):
            return IngestReport(source_key=agent.key, found=1, ingested=1)

        async def finish_sweep(self, reports, **kwargs):
            return True

    monkeypatch.setattr(pipeline_module, "IngestionPipeline", _FakePipeline)
    monkeypatch.setattr(app.agents, "get_agent",
                        lambda key, settings: SimpleNamespace(key=key, display_name=key))

    args = SimpleNamespace(source=["ted-eu"], limit=5, dry_run=True)
    await run_sweep.run(args, logging.getLogger("t"))
    assert called == []
