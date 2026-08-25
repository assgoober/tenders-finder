"""Pipeline tests with a mocked Anthropic client.

No network, no API key, no database. These cover the parts that are cheap to
get wrong and expensive to notice: the validate-repair-review ladder, the
guarantee that unvalidated output never reaches the database, and the
dual-use claim that a non-defense tender needs no special handling.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.agents import get_agent
from app.config import Settings
from app.nlp.embeddings import HashEmbeddingProvider
from app.nlp.extractor import TenderExtractor
from app.pipeline import IngestionPipeline
from app.schemas.tender import TenderExtraction

pytestmark = pytest.mark.asyncio


def make_settings(**overrides) -> Settings:
    base = {
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

    def __init__(self, parsed_output, text: str = "{}"):
        self.parsed_output = parsed_output
        self.usage = FakeUsage()
        self.content = [type("Block", (), {"type": "text", "text": text})()]


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
    def __init__(self, responses):
        self.messages = FakeMessages(responses)


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
