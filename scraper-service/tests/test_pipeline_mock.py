"""Pipeline tests with a mocked Anthropic client.

No network, no API key, no database. These cover the parts that are cheap to
get wrong and expensive to notice: the validate-repair-review ladder, the
guarantee that unvalidated output never reaches the database, and the
dual-use claim that a non-defense tender needs no special handling.
"""

from __future__ import annotations

import smtplib
from datetime import datetime, timezone

import pytest

from app.agents import get_agent
from app.config import Settings
from app.nlp.embeddings import HashEmbeddingProvider
from app.nlp.extractor import (
    FALLBACK_BETA,
    ExtractionNotConfigured,
    TenderExtractor,
)
from app.notifications import (
    EmailNotifier,
    build_digest,
    explain_smtp_failure,
    needs_attention,
)
from app.pipeline import IngestionPipeline, IngestReport
from app.schemas.tender import TenderExtraction

pytestmark = pytest.mark.asyncio


def make_settings(**overrides) -> Settings:
    base = {
        # Pinned, not inherited. `Settings()` reads the developer's .env, so
        # without this an EXTRACTION_PROVIDER set on the machine silently
        # changes which backend these tests exercise -- and the suite starts
        # passing or failing on ambient configuration. Same reason CI pins it.
        "extraction_provider": "anthropic",
        "anthropic_api_key": "test-key",
        "embedding_provider": "hash",
        "dry_run": True,
        "scrape_max_retries": 1,
        "scrape_backoff_base_seconds": 0.0,
    }
    base.update(overrides)
    return Settings(**base)


VALID_EXTRACTION = TenderExtraction(
    title="Supply of Ruggedized Tactical Radio Systems and Ancillaries",
    summary="Supply of 480 handheld tactical radios with support.",
    buyer_name="Directorate of Defense Procurement",
    deadline=datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc),
    extraction_confidence="high",
)


class FakeUsage:
    input_tokens = 1234
    output_tokens = 567


class FakeResponse:
    """Mimics the shape the SDK's `messages.parse` returns."""

    def __init__(
        self,
        parsed_output,
        text: str = "{}",
        *,
        stop_reason: str | None = None,
        stop_details=None,
        model: str | None = None,
    ):
        self.parsed_output = parsed_output
        self.usage = FakeUsage()
        self.content = [type("Block", (), {"type": "text", "text": text})()]
        self.stop_reason = stop_reason
        self.stop_details = stop_details
        self.model = model


def refusal(category: str | None = "cyber", **kwargs) -> FakeResponse:
    """A declined request: HTTP 200, empty content, no parsed output."""
    details = None if category is None else type("Details", (), {"category": category})()
    response = FakeResponse(None, stop_reason="refusal", stop_details=details, **kwargs)
    response.content = []  # a pre-output decline carries nothing
    return response


class FakeMessages:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict] = []

    async def parse(self, **kwargs):
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("parse() called more times than the test scripted")
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakeClient:
    """Exposes `messages` and `beta.messages` over one recorder.

    The extractor calls the beta namespace when fallbacks are enabled and the
    stable one when they are not; sharing the recorder keeps both paths under
    the same assertions.
    """

    def __init__(self, responses):
        self.messages = FakeMessages(responses)
        self.beta = type("Beta", (), {"messages": self.messages})()


# --- Extractor ---------------------------------------------------------------


async def test_extract_returns_validated_model_on_first_attempt():
    client = FakeClient([FakeResponse(VALID_EXTRACTION)])
    extractor = TenderExtractor(make_settings(), client=client)

    result = await extractor.extract("SOLICITATION No. MOD-2026-0417 ...")

    assert result.ok
    assert result.status == "extracted"
    assert result.attempts == 1
    assert result.extraction.title.startswith("Supply of Ruggedized")
    assert result.input_tokens == 1234


async def test_extract_repairs_once_then_succeeds():
    """A first-attempt schema violation triggers exactly one repair round."""
    client = FakeClient([FakeResponse(None), FakeResponse(VALID_EXTRACTION)])
    extractor = TenderExtractor(make_settings(), client=client)

    result = await extractor.extract("some tender text")

    assert result.ok
    assert result.attempts == 2

    # The repair turn must carry the original draft and the errors, and must
    # not end on an assistant turn (prefill is rejected on current models).
    repair_messages = client.messages.calls[1]["messages"]
    assert [m["role"] for m in repair_messages] == ["user", "assistant", "user"]
    assert "failed schema validation" in repair_messages[-1]["content"]


async def test_two_failures_route_to_human_review_and_never_persist():
    client = FakeClient([FakeResponse(None), FakeResponse(None)])
    extractor = TenderExtractor(make_settings(), client=client)

    result = await extractor.extract("unreadable scanned document")

    assert result.status == "needs_review"
    assert result.extraction is None
    assert not result.ok


async def test_malformed_payload_is_rejected_not_coerced():
    """Defense in depth: a dict that does not satisfy the schema is refused
    even if a client hands it over as `parsed_output`."""
    bad = {"summary": "missing the required title and confidence fields"}
    client = FakeClient([FakeResponse(bad), FakeResponse(bad)])
    extractor = TenderExtractor(make_settings(), client=client)

    result = await extractor.extract("text")

    assert result.status == "needs_review"
    assert result.extraction is None
    assert "title" in (result.error or "")


async def test_api_error_fails_without_retrying_forever():
    client = FakeClient([RuntimeError("503 upstream unavailable")])
    extractor = TenderExtractor(make_settings(), client=client)

    result = await extractor.extract("text")

    assert result.status == "failed"
    assert "RuntimeError" in result.error


async def test_empty_document_short_circuits():
    client = FakeClient([])
    result = await TenderExtractor(make_settings(), client=client).extract("   ")

    assert result.status == "failed"
    assert client.messages.calls == []


async def test_system_prompt_is_cached_and_document_is_not():
    """Prompt caching only pays off if the document sits after the
    breakpoint — otherwise every tender writes a new cache entry."""
    client = FakeClient([FakeResponse(VALID_EXTRACTION)])
    await TenderExtractor(make_settings(), client=client).extract("tender text")

    call = client.messages.calls[0]
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "tender text" in call["messages"][0]["content"]


# --- Refusals ----------------------------------------------------------------


async def test_refusal_routes_to_review_and_is_not_retried():
    """A policy decline is a 200 with empty content, not an exception.

    It must not be retried: the same prompt over the same document draws the
    same decline, so a repair round only spends money. And it must not be
    reported as a malformed answer — that would send someone hunting for a
    schema bug that does not exist.
    """
    client = FakeClient([refusal("cyber")])
    extractor = TenderExtractor(make_settings(), client=client)

    result = await extractor.extract("SOLICITATION: secure comms subsystem ...")

    assert result.status == "needs_review"
    assert result.refused
    assert result.refusal_category == "cyber"
    assert result.attempts == 1
    assert len(client.messages.calls) == 1, "a refusal must not trigger a repair round"


async def test_refusal_without_a_category_is_still_a_refusal():
    """stop_details is informational and can be absent. Branching on it
    instead of stop_reason would let this one through as an empty answer."""
    client = FakeClient([refusal(None)])
    result = await TenderExtractor(make_settings(), client=client).extract("text")

    assert result.status == "needs_review"
    assert result.refusal_category == "unspecified"
    assert "no structured output" not in (result.error or "")


async def test_fallbacks_are_requested_by_default():
    client = FakeClient([FakeResponse(VALID_EXTRACTION, model="claude-opus-4-8")])
    result = await TenderExtractor(make_settings(), client=client).extract("text")

    call = client.messages.calls[0]
    assert call["fallbacks"] == "default"
    assert call["betas"] == [FALLBACK_BETA]
    # A rescue must be visible: `served_by` differs from the configured model.
    assert result.served_by == "claude-opus-4-8"
    assert result.ok


async def test_fallbacks_can_be_switched_off():
    client = FakeClient([FakeResponse(VALID_EXTRACTION)])
    settings = make_settings(anthropic_fallbacks=False)
    result = await TenderExtractor(settings, client=client).extract("text")

    assert result.ok
    assert "fallbacks" not in client.messages.calls[0]
    assert "betas" not in client.messages.calls[0]


# --- Embeddings --------------------------------------------------------------


async def test_hash_embeddings_are_deterministic_and_correctly_sized():
    provider = HashEmbeddingProvider(make_settings())
    first, second = await provider.embed(["tactical radio MIL-STD-810H"] * 2)

    assert first == second
    assert len(first) == 1024
    assert abs(sum(v * v for v in first) - 1.0) < 1e-6


async def test_embedding_of_empty_text_is_not_a_zero_vector():
    """Cosine distance against a zero vector is undefined; pgvector would
    return NaN and the match would score as garbage rather than as absent."""
    provider = HashEmbeddingProvider(make_settings())
    [vector] = await provider.embed([""])

    assert any(v != 0 for v in vector)


# --- Pipeline ----------------------------------------------------------------


async def test_pipeline_ingests_fixture_source_end_to_end():
    settings = make_settings()
    client = FakeClient([FakeResponse(VALID_EXTRACTION), FakeResponse(VALID_EXTRACTION)])

    pipeline = IngestionPipeline(
        settings,
        extractor=TenderExtractor(settings, client=client),
        embedder=HashEmbeddingProvider(settings),
        repository=None,
    )
    report = await pipeline.run_source(get_agent("sample-fixtures", settings))

    assert report.found == 2
    assert report.ingested == 2
    assert report.failed == 0


async def test_needs_review_tender_is_not_embedded():
    """An unvalidated tender must stay out of the matching corpus, or the
    alert engine notifies people about data nobody has checked."""
    settings = make_settings()
    client = FakeClient([FakeResponse(None), FakeResponse(None)])

    pipeline = IngestionPipeline(
        settings,
        extractor=TenderExtractor(settings, client=client),
        embedder=HashEmbeddingProvider(settings),
        repository=None,
    )

    agent = get_agent("sample-fixtures", settings)
    stub = (await agent.discover(limit=1))[0]
    fetched = await agent.fetch(stub)

    from app.pipeline import IngestReport

    report = IngestReport(source_key=agent.key)
    await pipeline._ingest_one(fetched, None, report)

    assert report.needs_review == 1
    assert report.ingested == 0


async def test_row_falls_back_to_listing_metadata_when_extraction_is_empty():
    settings = make_settings()
    client = FakeClient([FakeResponse(None), FakeResponse(None)])
    pipeline = IngestionPipeline(
        settings,
        extractor=TenderExtractor(settings, client=client),
        embedder=HashEmbeddingProvider(settings),
        repository=None,
    )

    agent = get_agent("sample-fixtures", settings)
    stub = (await agent.discover(limit=1))[0]
    fetched = await agent.fetch(stub)
    result = await pipeline.extractor.extract(fetched.combined_text())

    row = pipeline._build_row(fetched, result, None, fetched.combined_text())

    assert row["title"] == stub.title
    assert row["deadline"] is not None
    assert row["extraction_status"] == "needs_review"
    assert row["metadata"]["extraction"]["attempts"] == 2


async def test_metadata_carries_the_full_pegs_body():
    row_metadata = VALID_EXTRACTION.to_metadata()

    assert set(row_metadata) >= {
        "participation",
        "evaluation",
        "guidelines",
        "selection",
        "sector_specific",
    }


async def test_embedding_text_excludes_boilerplate():
    """Only scope and technical content get vectorized — legal boilerplate
    is near-identical across a buyer's tenders and collapses the vectors."""
    extraction = VALID_EXTRACTION.model_copy(deep=True)
    extraction.guidelines.scope_summary = "480 handheld VHF/UHF radios"
    extraction.guidelines.referenced_standards = ["MIL-STD-810H"]
    extraction.participation.financial_requirements = "Turnover of USD 10,000,000"

    text = extraction.embedding_text()

    assert "MIL-STD-810H" in text
    assert "480 handheld" in text
    assert "Turnover" not in text


# --- Run digest --------------------------------------------------------------


def report(**kwargs) -> IngestReport:
    return IngestReport(source_key=kwargs.pop("source_key", "mod-balam"), **kwargs)


async def test_digest_is_silent_on_a_clean_run():
    """A mail that always arrives stops being read."""
    notifier = EmailNotifier(make_settings())
    sent = await notifier.send_run_digest([report(found=20, ingested=20)])

    assert sent is False


async def test_digest_fires_when_tenders_are_waiting_for_a_human():
    assert needs_attention([report(found=20, ingested=18, needs_review=2)])
    assert needs_attention([report(found=5, failed=1)])
    assert not needs_attention([report(found=5, ingested=5)])


async def test_digest_names_the_refusals_separately_from_schema_failures():
    """A wall of policy declines and a wall of unparseable scans call for
    different responses, so the digest must not merge them."""
    subject, body = build_digest(
        [report(found=10, ingested=6, needs_review=4, refused=3, errors=["x: boom"])]
    )

    assert "4 for review" in subject
    assert "3 declined by policy" in body
    assert "raise no" in body, "the digest must say review tenders generate no alerts"
    assert "x: boom" in body


async def test_unconfigured_smtp_does_not_raise():
    """Mail is an operational convenience. A pipeline that aborts because a
    mail server is unreachable has traded the job for a notice about it."""
    notifier = EmailNotifier(make_settings(smtp_host=None))
    sent = await notifier.send_run_digest([report(found=3, needs_review=3)])

    assert sent is False


async def test_smtp_failure_does_not_abort_the_run():
    class ExplodingNotifier(EmailNotifier):
        def _send(self, message):
            raise OSError("connection refused")

    notifier = ExplodingNotifier(
        make_settings(smtp_host="smtp.example.test", notify_email="ops@example.test")
    )
    assert await notifier.send_run_digest([report(found=1, needs_review=1)]) is False


async def test_old_sdk_without_fallbacks_still_extracts():
    """Losing the fallback is a degradation; losing extraction is an outage.

    An SDK too old to know the `fallbacks` kwarg raises TypeError. The
    extractor must fall through to the stable call rather than turn every
    document into a failure.
    """

    class PickyMessages(FakeMessages):
        async def parse(self, **kwargs):
            if "fallbacks" in kwargs:
                raise TypeError("unexpected keyword argument 'fallbacks'")
            return await super().parse(**kwargs)

    client = FakeClient([FakeResponse(VALID_EXTRACTION)])
    client.messages = PickyMessages([FakeResponse(VALID_EXTRACTION)])
    client.beta = type("Beta", (), {"messages": client.messages})()

    result = await TenderExtractor(make_settings(), client=client).extract("text")

    assert result.ok
    assert "fallbacks" not in client.messages.calls[-1]


async def test_smtp_failures_are_explained_not_just_reported():
    """A digest that fails to send is the one time the log has to be
    actionable — it is the only remaining record that tenders are waiting."""
    auth = smtplib.SMTPAuthenticationError(535, b"bad credentials")
    assert "App Password" in explain_smtp_failure(auth)

    sender = smtplib.SMTPSenderRefused(553, b"not owned", "x@y.test")
    assert "NOTIFY_FROM_EMAIL" in explain_smtp_failure(sender)

    assert "SMTP_HOST" in explain_smtp_failure(ConnectionRefusedError(111, "refused"))


# --- Missing ANTHROPIC_API_KEY is a configuration error, not a transport one ---
#
# Found by running the pipeline with no keys against the live BALAM portal:
# it scraped five real tenders and then failed all five with the Anthropic
# SDK's header error, `TypeError: "Could not resolve authentication method"`.
# Accurate about the SDK, silent about the cause, and repeated once per row —
# a nine-page sweep would produce ~190 of them and read like a client-library
# bug. These lock in the loud, correct failure instead.


async def test_run_refuses_before_touching_a_portal_without_a_key(monkeypatch):
    """The gate is in run(), not per tender.

    Asserted through an agent that fails the test if it is contacted at all:
    the point is not only that the run raises, but that it raises before the
    portal is asked for anything. A per-tender check would still make ~190
    requests to a government site to learn something knowable at startup.
    """
    contacted = []

    class Tripwire:
        key = "tripwire"
        display_name = "should never be reached"
        base_url = "https://example.invalid"
        country = "IL"

        async def discover(self, *a, **k):
            contacted.append(True)
            return []

    monkeypatch.setattr("app.pipeline.get_agents", lambda *a, **k: [Tripwire()])

    pipeline = IngestionPipeline(settings=make_settings(anthropic_api_key=""))

    with pytest.raises(ExtractionNotConfigured) as err:
        await pipeline.run(["tripwire"], limit=5)

    assert contacted == [], "the portal was contacted despite no API key"
    # The message has to name the variable; that is the whole repair.
    assert "ANTHROPIC_API_KEY" in str(err.value)


async def test_extractor_client_reports_the_cause_not_the_sdk_error():
    """Second layer, for callers that bypass the pipeline.

    `TenderExtractor` is reachable from the /extract route and from scripts,
    so the pipeline gate alone would leave the confusing error in place on
    those paths.
    """
    extractor = TenderExtractor(make_settings(anthropic_api_key=""))

    with pytest.raises(ExtractionNotConfigured):
        _ = extractor.client


async def test_a_mocked_client_still_bypasses_the_gate():
    """The guard must not break the way every other test here runs.

    An injected client means the key is irrelevant, and that has to keep
    being true or the suite would need a real credential to run.
    """
    sentinel = object()
    extractor = TenderExtractor(make_settings(anthropic_api_key=""), client=sentinel)
    assert extractor.client is sentinel
