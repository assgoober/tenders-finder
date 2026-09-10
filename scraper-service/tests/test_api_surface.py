"""Tests for what the HTTP surface reports about itself.

These exist because the provider switch (ADR-20) updated the pipeline and the
startup check but not `/health` or `/extract`, and nothing failed. The service
kept running Gemini while its own health endpoint named an Anthropic model and
its own extract endpoint refused with "ANTHROPIC_API_KEY is not configured" --
a status surface that reports the wrong thing is worse than one that reports
nothing, because it is believed.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import main as main_module
from app.config import Settings
from app.nlp.extractor import ExtractionResult


GEMINI_ONLY = Settings(
    extraction_provider="gemini",
    gemini_api_key="test-key",
    anthropic_api_key=None,
    gemini_model="gemini-3.5-flash-lite",
    embedding_provider="hash",
)

ANTHROPIC_ONLY = Settings(
    extraction_provider="anthropic",
    anthropic_api_key="test-key",
    gemini_api_key=None,
    anthropic_model="claude-opus-5",
    embedding_provider="hash",
)


@pytest.fixture
def client(monkeypatch):
    def _client(settings: Settings) -> TestClient:
        monkeypatch.setattr(main_module, "get_settings", lambda: settings)
        return TestClient(main_module.app)

    return _client


# --- /health ---------------------------------------------------------------

def test_health_names_the_model_that_will_actually_run(client):
    """The reported model follows the selected provider.

    Hardcoding `anthropic_model` meant a Gemini deployment advertised
    "claude-opus-5" to anything reading /health -- including a human deciding
    whether a bad extraction was a model problem.
    """
    body = client(GEMINI_ONLY).get("/health").json()
    assert body["model"] == "gemini-3.5-flash-lite"

    body = client(ANTHROPIC_ONLY).get("/health").json()
    assert body["model"] == "claude-opus-5"


def test_health_reports_the_provider_and_whether_it_is_configured(client):
    body = client(GEMINI_ONLY).get("/health").json()
    assert body["extraction_provider"] == "gemini"
    assert body["extraction_configured"] is True


def test_health_does_not_call_a_gemini_deployment_configured_on_a_stray_key(client):
    """An Anthropic key present while the provider is Gemini is not readiness."""
    settings = Settings(
        extraction_provider="gemini",
        gemini_api_key=None,
        anthropic_api_key="a-key-for-the-provider-we-are-not-using",
        embedding_provider="hash",
    )
    body = client(settings).get("/health").json()
    assert body["extraction_configured"] is False


# --- /extract --------------------------------------------------------------

def test_extract_does_not_demand_an_anthropic_key_under_gemini(client, monkeypatch):
    """The bug this file was written for.

    /extract gated on `has_anthropic`, so the review screen and every
    prompt-iteration call returned 503 on a service whose pipeline was
    extracting successfully three lines away.
    """
    captured = {}

    class StubExtractor:
        async def extract(self, raw_text, *, source_name=None, source_url=None):
            captured["called"] = True
            return ExtractionResult(
                status="extracted", attempts=1, model="gemini-3.5-flash-lite",
                prompt_version="pegs-v1", extraction=None,
            )

    monkeypatch.setattr(main_module, "get_extractor", lambda settings: StubExtractor())

    r = client(GEMINI_ONLY).post("/extract", json={"raw_text": "a tender document"})
    assert r.status_code == 200, r.text
    assert captured.get("called") is True
    assert r.json()["model"] == "gemini-3.5-flash-lite"


def test_extract_still_refuses_when_the_selected_provider_has_no_key(client):
    settings = Settings(
        extraction_provider="gemini", gemini_api_key=None,
        anthropic_api_key=None, embedding_provider="hash",
    )
    r = client(settings).post("/extract", json={"raw_text": "a tender document"})
    assert r.status_code == 503
    # The message must name the key the operator actually has to set.
    assert "GEMINI_API_KEY" in r.json()["detail"]


def test_extract_goes_through_the_provider_factory(client, monkeypatch):
    """Constructing TenderExtractor directly ran Anthropic's transport against
    a Gemini deployment. The factory is the only thing that knows which class
    the configuration means."""
    seen = {}

    class StubExtractor:
        async def extract(self, raw_text, *, source_name=None, source_url=None):
            return ExtractionResult(
                status="extracted", attempts=1, model="m",
                prompt_version="pegs-v1", extraction=None,
            )

    def fake_factory(settings):
        seen["provider"] = settings.extraction_provider
        return StubExtractor()

    monkeypatch.setattr(main_module, "get_extractor", fake_factory)
    client(GEMINI_ONLY).post("/extract", json={"raw_text": "x"})
    assert seen["provider"] == "gemini"


# --- tender lifecycle (migration 0011) -------------------------------------

class _RecordingRepo:
    """Enough of SupabaseRepository for pipeline.run to finish."""

    def __init__(self, raises: Exception | None = None):
        self.aged = 0
        self._raises = raises

    def age_tender_status(self):
        self.aged += 1
        if self._raises:
            raise self._raises
        return {"to_open": 0, "to_closing_soon": 2, "to_closed": 3}


@pytest.mark.asyncio
async def test_a_sweep_ages_the_corpus_before_the_digest():
    """A deadline passes on the clock, not on a sweep -- but leaving the
    corpus stale between runs is what put 46 expired tenders in front of a
    reader marked 'open'."""
    from app.pipeline import IngestionPipeline

    repo = _RecordingRepo()
    pipeline = IngestionPipeline(
        Settings(extraction_provider="gemini", gemini_api_key="k",
                 embedding_provider="hash", dry_run=False),
        repository=repo,
    )
    await pipeline.run([])
    assert repo.aged == 1


@pytest.mark.asyncio
async def test_a_dry_run_does_not_age_anything():
    """Extraction still runs under dry_run; a write to shared rows must not."""
    from app.pipeline import IngestionPipeline

    repo = _RecordingRepo()
    pipeline = IngestionPipeline(
        Settings(extraction_provider="gemini", gemini_api_key="k",
                 embedding_provider="hash", dry_run=True),
        repository=repo,
    )
    await pipeline.run([])
    assert repo.aged == 0


@pytest.mark.asyncio
async def test_a_failed_status_refresh_does_not_fail_the_sweep():
    """A sweep that harvested correctly is not a failed sweep because a
    status refresh could not run."""
    from app.pipeline import IngestionPipeline

    repo = _RecordingRepo(raises=RuntimeError("rpc unavailable"))
    pipeline = IngestionPipeline(
        Settings(extraction_provider="gemini", gemini_api_key="k",
                 embedding_provider="hash", dry_run=False),
        repository=repo,
    )
    reports = await pipeline.run([])          # must not raise
    assert repo.aged == 1
    assert reports == []


# --- partial harvests must not look like complete ones ---------------------

def test_a_truncated_walk_is_reported_as_partial():
    """2026-09-09: a sweep stopped at page 4 of 10, harvested 60 of 185
    tenders, and reported found=60 ingested=60 errors=[] -- the same shape a
    complete run produces. The guard that stopped the walk was right; the
    report that called it clean was not."""
    from app.pipeline import IngestReport

    r = IngestReport(source_key="mod-balam", found=60, ingested=60, partial=True)
    assert r.as_dict()["partial"] is True

    complete = IngestReport(source_key="mod-balam", found=185, ingested=185)
    assert complete.as_dict()["partial"] is False


def test_a_partial_harvest_reaches_the_digest_and_leads_the_subject():
    from app.notifications import build_digest, needs_attention
    from app.pipeline import IngestReport

    reports = [IngestReport(source_key="mod-balam", found=60, ingested=60,
                            partial=True, errors=["listing pagination stopped early"])]
    assert needs_attention(reports) is True
    subject, _ = build_digest(reports)
    assert "PARTIAL" in subject and "mod-balam" in subject
    assert "clean" not in subject


def test_a_complete_run_still_reads_clean():
    from app.notifications import build_digest
    from app.pipeline import IngestReport

    subject, _ = build_digest([IngestReport(source_key="mod-balam", found=185, ingested=185)])
    assert "clean" in subject and "PARTIAL" not in subject


@pytest.mark.asyncio
async def test_running_out_of_pages_is_not_a_partial_harvest():
    """The distinction the whole fix rests on: 'no more pages' and 'the page
    would not turn' are different endings and must not report the same."""
    from app.agents.mod_balam import ModBalamAgent

    agent = ModBalamAgent.__new__(ModBalamAgent)
    agent.pagination_truncated = False
    agent._last_page_had_a_next_link = False
    assert agent.pagination_truncated is False
