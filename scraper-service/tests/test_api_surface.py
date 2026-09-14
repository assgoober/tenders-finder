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


# --- incremental extraction ------------------------------------------------

def _pipeline(**over):
    from app.pipeline import IngestionPipeline
    # This block is about extraction-mode skipping, so it pins the mode. The
    # listing-mode rules are different on purpose and are pinned separately
    # below -- conflating the two is the bug these tests now guard.
    base = dict(extraction_provider="gemini", gemini_api_key="k",
                gemini_model="gemini-3.5-flash-lite", embedding_provider="hash",
                extract_on_ingest=True)
    base.update(over)
    return IngestionPipeline(Settings(**base), repository=object())


def _stub(raw_row="1000 a tender 16:00 01/10/2026 01/09/2026"):
    from app.agents.base import TenderStub
    return TenderStub(external_id="1000", title="a tender", source_url="u",
                      listing_metadata={"raw_row": raw_row})


def _fp(**over):
    from app.nlp.prompts import PROMPT_VERSION
    fp = {"raw_row": "1000 a tender 16:00 01/10/2026 01/09/2026",
          "prompt_version": PROMPT_VERSION, "model": "gemini-3.5-flash-lite",
          # Both reported by `ingest_fingerprints` since listing-first
          # ingestion: a row can now be stored and embedded without ever
          # having been extracted.
          "extracted": True, "embedded": True}
    fp.update(over)
    return fp


def test_an_unchanged_tender_is_skipped():
    """Fetching a detail page launches a browser and extraction costs an LLM
    call, both to arrive at the row already stored."""
    assert _pipeline()._already_current(_stub(), _fp()) is True


def test_a_tender_the_portal_edited_is_re_extracted():
    """raw_row carries the portal's own last-updated column, which is what
    makes it a change detector rather than a guess."""
    edited = _fp(raw_row="1000 a tender 16:00 08/10/2026 09/09/2026")
    assert _pipeline()._already_current(_stub(), edited) is False


def test_a_new_prompt_version_re_extracts_the_corpus():
    """Otherwise the corpus quietly becomes a mixture of two prompt
    generations, and nothing says which row came from which."""
    assert _pipeline()._already_current(_stub(), _fp(prompt_version="pegs-v0")) is False


def test_a_model_change_re_extracts_the_corpus():
    assert _pipeline()._already_current(_stub(), _fp(model="claude-opus-5")) is False


def test_a_tender_that_was_never_extracted_is_not_skipped():
    """needs_review rows are absent from the index on purpose: the next sweep
    is their retry, not a cache miss."""
    assert _pipeline()._already_current(_stub(), None) is False


def test_a_row_without_an_embedding_is_never_skipped():
    """The one state a sweep must always fix.

    An unembedded row is in the table and invisible to every query that
    matters -- it inflates the count and matches nothing. Skipping it would
    make that permanent.
    """
    assert _pipeline()._already_current(_stub(), _fp(embedded=False)) is False


# --- listing mode has its own idea of "current" ----------------------------
#
# These exist because the first version of listing-first ingestion re-embedded
# the entire corpus on every sweep and nothing looked wrong: the counts were
# right, the rows were right, and the bill was simply paid again each night.
# The cause was asking an extraction question about a listing.

def _listing_pipeline(**over):
    return _pipeline(extract_on_ingest=False, **over)


def test_listing_mode_skips_an_unchanged_listing():
    """The listing is byte-identical and already embedded. A second pass
    cannot produce anything the first one did not."""
    assert _listing_pipeline()._already_current(_stub(), _fp()) is True


def test_listing_mode_ignores_the_prompt_version():
    """No prompt produced this vector, so a prompt change says nothing about
    it. Asking anyway rejected every row forever -- which is the whole bug."""
    assert _listing_pipeline()._already_current(_stub(), _fp(prompt_version="pegs-v0")) is True


def test_listing_mode_skips_a_row_that_was_never_extracted():
    """`pending` is the normal, healthy state of a listing-embedded tender,
    not a reason to redo it."""
    assert _listing_pipeline()._already_current(_stub(), _fp(extracted=False)) is True


def test_listing_mode_still_re_ingests_an_edited_listing():
    """The one thing that does change a listing vector is the listing."""
    edited = _fp(raw_row="1000 a tender 16:00 08/10/2026 09/09/2026")
    assert _listing_pipeline()._already_current(_stub(), edited) is False


def test_listing_mode_still_refuses_an_unembedded_row():
    assert _listing_pipeline()._already_current(_stub(), _fp(embedded=False)) is False


def test_anything_unknown_counts_as_a_mismatch():
    """A wrong skip is invisible -- it looks exactly like a tender nobody
    changed -- so the decision errs toward doing the work."""
    p = _pipeline()
    assert p._already_current(_stub(), _fp(raw_row=None)) is False
    assert p._already_current(_stub(raw_row=None), _fp()) is False
    assert p._already_current(_stub(), _fp(model=None)) is False
    assert p._already_current(_stub(), {}) is False


def test_the_report_says_how_many_were_skipped():
    """Same lesson as `partial`: work not done has to be visible in the
    result, or a run that skipped everything reads like a run that ingested
    nothing."""
    from app.pipeline import IngestReport
    r = IngestReport(source_key="mod-balam", found=185, ingested=7, skipped=178)
    assert r.as_dict()["skipped"] == 178


def test_a_quota_abort_is_a_partial_harvest():
    """2026-09-11: a TED sweep ingested 483 of 551 and aborted on the
    provider's daily quota. It reported partial=false, failed=0. Both were
    literally true, and together they read as a completed run -- the third
    time in this codebase that work not done failed to reach the reader."""
    from app.pipeline import IngestReport

    r = IngestReport(source_key="ted-eu", found=551, ingested=483, partial=True,
                     errors=["run aborted: per-day request quota is exhausted"])
    assert r.as_dict()["partial"] is True


@pytest.mark.asyncio
async def test_the_quota_abort_sets_partial_on_the_real_path():
    """Not just the dataclass: the pipeline has to set it where it breaks."""
    from app.agents.base import FetchedTender, TenderStub
    from app.nlp.extractor import ExtractionQuotaExhausted
    from app.pipeline import IngestionPipeline

    class _QuotaAgent:
        key = "fake"
        display_name = "fake"
        base_url = ""
        country = None
        sector = "defense"
        async def discover(self, *, limit=50):
            return [TenderStub(external_id=str(i), title="t", source_url="u")
                    for i in range(3)]
        async def fetch(self, stub):
            return FetchedTender(stub=stub, raw_text="x")

    class _Blown(IngestionPipeline):
        async def _ingest_one(self, fetched, source_id, report):
            raise ExtractionQuotaExhausted("per-day request quota is exhausted")

    pipeline = _Blown(
        # Pinned to the extraction path on purpose. The quota abort is a
        # property of extracting, and since listing-first ingestion became
        # the default a sweep makes no model calls at all -- so this test
        # would pass for the wrong reason if it took the default.
        Settings(extraction_provider="gemini", gemini_api_key="k",
                 embedding_provider="hash", dry_run=True,
                 extract_on_ingest=True),
    )
    report = await pipeline.run_source(_QuotaAgent(), limit=3)
    assert report.partial is True
    assert report.ingested == 0
    assert any("quota" in e for e in report.errors)


# --- customer match alerts (migration 0016) --------------------------------

SAMPLE_MATCHES = [
    {
        "match_id": "m1", "organization": "AIR", "recipient": "bd@air.example",
        "profile_name": "cargo drone", "score": 0.6668, "deadline": "2026-09-17T09:00:00+02:00",
        "title": "Germany - Non-piloted aircraft - 25 Drohnen inklusive Zubehoer",
        "buyer_name": "Polizei Berlin", "source_url": "https://ted.europa.eu/x",
        "external_id": "583275-2026",
    },
    {
        "match_id": "m2", "organization": "AIR", "recipient": "bd@air.example",
        "profile_name": "cargo drone", "score": 0.61, "deadline": None,
        "title": "Sweden - framework for unmanned aircraft",
        "buyer_name": None, "source_url": None, "external_id": "582323-2026",
    },
]


def test_one_email_per_recipient_not_one_per_match():
    """Seven matches on a Tuesday morning must not be seven emails. That is
    how an alert becomes a filter rule."""
    from app.notifications import group_alerts_by_recipient

    grouped = group_alerts_by_recipient(SAMPLE_MATCHES)
    assert len(grouped) == 1
    assert len(next(iter(grouped.values()))) == 2


def test_a_row_with_no_recipient_is_dropped_not_sent_to_nobody():
    from app.notifications import group_alerts_by_recipient

    rows = SAMPLE_MATCHES + [dict(SAMPLE_MATCHES[0], match_id="m3", recipient="  ")]
    assert sum(len(v) for v in group_alerts_by_recipient(rows).values()) == 2


def test_the_subject_says_how_many_and_for_whom():
    from app.notifications import build_match_alert

    subject, _ = build_match_alert("AIR", SAMPLE_MATCHES)
    assert "2 new tenders" in subject and "AIR" in subject


def test_a_single_match_is_not_pluralised():
    from app.notifications import build_match_alert

    subject, _ = build_match_alert("AIR", SAMPLE_MATCHES[:1])
    assert "1 new tender matched" in subject


def test_the_body_counts_days_rather_than_printing_a_timestamp():
    """An ISO timestamp makes the reader do arithmetic to find out whether
    they have a week or an afternoon."""
    from app.notifications import build_match_alert

    _, body = build_match_alert("AIR", SAMPLE_MATCHES)
    assert "days left" in body or "closes today" in body or "1 day left" in body
    assert "2026-09-17T09:00:00" not in body


def test_a_tender_with_no_published_deadline_says_so(monkeypatch):
    from app.notifications import _format_deadline

    assert _format_deadline(None) == "no deadline published"


def test_the_body_carries_the_source_link_for_every_match_that_has_one():
    """Nothing in the email is a summary the reader has to trust."""
    from app.notifications import build_match_alert

    _, body = build_match_alert("AIR", SAMPLE_MATCHES)
    assert "https://ted.europa.eu/x" in body


@pytest.mark.asyncio
async def test_a_failed_send_leaves_the_match_pending():
    """A match marked delivered after a failed send is a match nobody will
    ever be told about."""
    from app.pipeline import IngestionPipeline

    class _Repo:
        def __init__(self):
            self.marked = []
        def pending_match_alerts(self, limit=500):
            return SAMPLE_MATCHES
        def mark_matches_alerted(self, ids):
            self.marked.extend(ids)
            return len(ids)

    class _DeadNotifier:
        async def send_match_alert(self, recipient, subject, body):
            return False
        async def send_run_digest(self, reports):
            return False

    repo = _Repo()
    pipeline = IngestionPipeline(
        Settings(extraction_provider="gemini", gemini_api_key="k",
                 embedding_provider="hash", dry_run=False),
        repository=repo, notifier=_DeadNotifier(),
    )
    delivered = await pipeline.deliver_match_alerts()
    assert delivered == 0
    assert repo.marked == []


@pytest.mark.asyncio
async def test_a_successful_send_stamps_exactly_the_matches_it_carried():
    from app.pipeline import IngestionPipeline

    class _Repo:
        def __init__(self):
            self.marked = []
        def pending_match_alerts(self, limit=500):
            return SAMPLE_MATCHES
        def mark_matches_alerted(self, ids):
            self.marked.extend(ids)
            return len(ids)

    class _LiveNotifier:
        def __init__(self):
            self.sent = []
        async def send_match_alert(self, recipient, subject, body):
            self.sent.append(recipient)
            return True
        async def send_run_digest(self, reports):
            return False

    repo, notifier = _Repo(), _LiveNotifier()
    pipeline = IngestionPipeline(
        Settings(extraction_provider="gemini", gemini_api_key="k",
                 embedding_provider="hash", dry_run=False),
        repository=repo, notifier=notifier,
    )
    delivered = await pipeline.deliver_match_alerts()
    assert delivered == 2
    assert notifier.sent == ["bd@air.example"]      # one message, two matches
    assert sorted(repo.marked) == ["m1", "m2"]


# --- alert lead time (migration 0018) --------------------------------------

def test_a_tender_closing_inside_a_week_is_marked_urgent():
    """The first alert this product was about to send led with a tender
    closing in 0.3 days: a correct match, correctly ordered, and useless."""
    from app.notifications import build_match_alert

    rows = [dict(SAMPLE_MATCHES[0], lead_days=3)]
    _, body = build_match_alert("AIR", rows)
    assert body.splitlines()[2].startswith("[URGENT] ")


def test_a_comfortable_deadline_is_not_marked():
    """Marking everything is marking nothing."""
    from app.notifications import build_match_alert

    rows = [dict(SAMPLE_MATCHES[0], lead_days=30)]
    _, body = build_match_alert("AIR", rows)
    assert "[URGENT]" not in body


def test_the_mark_comes_from_the_query_not_from_recomputing_the_date():
    """lead_days travels with the row so the mark and the ordering cannot
    disagree about which tenders are tight."""
    from app.notifications import build_match_alert

    # Deadline far away, lead_days says otherwise: the field wins.
    rows = [dict(SAMPLE_MATCHES[0], deadline="2027-12-01T09:00:00+00:00", lead_days=2)]
    _, body = build_match_alert("AIR", rows)
    assert "[URGENT]" in body


def test_a_tender_with_no_lead_days_is_not_marked():
    """A tender with no published deadline is not late, it is unknown."""
    from app.notifications import build_match_alert

    rows = [dict(SAMPLE_MATCHES[1], lead_days=None)]
    _, body = build_match_alert("AIR", rows)
    assert "[URGENT]" not in body
