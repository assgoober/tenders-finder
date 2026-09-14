"""Gemini backend tests, with a fake client. No network, no API key.

The ladder itself (constrained decode -> one repair round -> needs_review) is
tested against the Anthropic backend in `test_pipeline_mock.py` and is shared
code, so it is not re-tested here. What is tested is the part that is
genuinely different and genuinely easy to get wrong: telling a policy decline
apart from a truncated answer, and doing it before anything reads the output.

That distinction is not academic. A decline and a truncation both arrive as
HTTP 200 with unusable content. Routed as a decline, a tender is parked with
"no retry will help"; routed as a schema failure, it gets a repair round. Put
them in the wrong buckets and the review queue's whole split (ADR-15) lies.
"""

from __future__ import annotations

import json

import pytest

from app.config import Settings
from app.nlp.extractor import ExtractionNotConfigured, TenderExtractor, get_extractor
from app.nlp.gemini import GeminiExtractor, verify_gemini_model
from app.schemas.tender import TenderExtraction

pytestmark = pytest.mark.asyncio


def make_settings(**overrides) -> Settings:
    base = {
        # Every value these tests assert on is pinned here. `Settings()` reads
        # the developer's .env, so anything left unpinned makes the suite
        # depend on ambient machine configuration -- which is how changing
        # GEMINI_MODEL in a local .env silently changed what these tests were
        # checking. Same lesson as EXTRACTION_PROVIDER, learned twice.
        "extraction_provider": "gemini",
        "gemini_model": "gemini-3.6-flash",
        "gemini_api_key": "test-key",
        "embedding_provider": "hash",
        "dry_run": True,
    }
    base.update(overrides)
    return Settings(**base)


VALID_PAYLOAD = {
    "title": "Supply of Ruggedized Tactical Radio Systems",
    "summary": "Supply of 480 handheld tactical radios with support.",
    "buyer_name": "Directorate of Defense Procurement",
    "extraction_confidence": "high",
}


# --- fakes shaped like the SDK's response objects ---------------------------


class _Enum:
    """Stands in for the SDK's enums, which expose `.value`."""

    def __init__(self, value: str) -> None:
        self.value = value


class _Usage:
    def __init__(self, prompt=None, candidates=None, thoughts=None) -> None:
        self.prompt_token_count = prompt
        self.candidates_token_count = candidates
        self.thoughts_token_count = thoughts


class _Candidate:
    def __init__(self, finish_reason=None) -> None:
        self.finish_reason = _Enum(finish_reason) if finish_reason else None


class _Feedback:
    def __init__(self, block_reason=None) -> None:
        self.block_reason = _Enum(block_reason) if block_reason else None


class _Response:
    def __init__(self, text=None, *, finish_reason="STOP", block_reason=None, usage=None):
        self._text = text
        self.candidates = [] if block_reason else [_Candidate(finish_reason)]
        self.prompt_feedback = _Feedback(block_reason)
        self.usage_metadata = usage

    @property
    def text(self):
        if not self.candidates:
            # The SDK raises here rather than returning None. `_raw_text` is
            # called on the refusal path too, so it has to survive this.
            raise ValueError("no candidates in response")
        return self._text


class _FakeClient:
    """Records calls and replays a queued list of responses."""

    def __init__(self, *responses) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []
        self.aio = self

    @property
    def models(self):
        return self

    async def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        return self._responses.pop(0)


# --- the distinction the whole backend turns on -----------------------------


async def test_blocked_prompt_is_a_decline_not_a_schema_failure():
    """A blocked prompt returns 200 with no candidates at all.

    Reading the output first would report "model returned no structured
    output" -- the exact misreport ADR-13 was written about, in Google's
    shape. It must come back as needs_review carrying the category.
    """
    client = _FakeClient(_Response(block_reason="PROHIBITED_CONTENT"))
    result = await GeminiExtractor(make_settings(), client=client).extract("a tender")

    assert result.status == "needs_review"
    assert result.refusal_category == "prohibited_content"
    assert result.refused
    # No repair round: the same prompt would be declined again.
    assert len(client.calls) == 1


async def test_safety_finish_reason_is_a_decline():
    """The other door: generation started, then policy stopped it."""
    client = _FakeClient(_Response("{}", finish_reason="SAFETY"))
    result = await GeminiExtractor(make_settings(), client=client).extract("a tender")

    assert result.status == "needs_review"
    assert result.refusal_category == "safety"


async def test_max_tokens_is_NOT_treated_as_a_decline():
    """The inverse error, and the more dangerous one.

    MAX_TOKENS is an ordinary truncation. Calling it a policy decline would
    park a recoverable tender in the half of the review queue labelled "no
    retry will ever help", and it would never be retried.
    """
    truncated = _Response('{"title": "Supply of', finish_reason="MAX_TOKENS")
    client = _FakeClient(truncated, truncated)
    result = await GeminiExtractor(make_settings(), client=client).extract("a tender")

    assert result.refusal_category is None, "a truncated answer is not a policy decline"
    assert result.status == "needs_review"
    # It got the repair round a decline would not have got.
    assert len(client.calls) == 2


async def test_no_candidates_and_no_stated_reason_is_still_not_a_schema_bug():
    client = _FakeClient(_Response(None, block_reason=None))
    client._responses = [_Response.__new__(_Response)]
    empty = client._responses[0]
    empty._text = None
    empty.candidates = []
    empty.prompt_feedback = _Feedback(None)
    empty.usage_metadata = None

    result = await GeminiExtractor(make_settings(), client=client).extract("a tender")
    assert result.status == "needs_review"
    assert result.refusal_category == "unspecified"


# --- ordinary paths ---------------------------------------------------------


async def test_valid_json_is_validated_and_returned():
    client = _FakeClient(_Response(json.dumps(VALID_PAYLOAD)))
    result = await GeminiExtractor(make_settings(), client=client).extract("a tender")

    assert result.status == "extracted"
    assert isinstance(result.extraction, TenderExtraction)
    assert result.model == "gemini-3.6-flash"


async def test_invalid_then_valid_takes_the_repair_round():
    client = _FakeClient(
        _Response(json.dumps({"extraction_confidence": "not-a-level"})),
        _Response(json.dumps(VALID_PAYLOAD)),
    )
    result = await GeminiExtractor(make_settings(), client=client).extract("a tender")

    assert result.status == "extracted"
    assert result.attempts == 2
    # The repair turn must echo the draft back as a `model` turn -- Gemini's
    # name for the assistant role. Sending "assistant" is a 400.
    roles = [c.role for c in client.calls[1]["contents"]]
    assert roles == ["user", "model", "user"]


async def test_thinking_tokens_count_as_output():
    """Left out, a run's cost reads lower than the invoice."""
    client = _FakeClient(
        _Response(json.dumps(VALID_PAYLOAD), usage=_Usage(prompt=1000, candidates=200, thoughts=800))
    )
    result = await GeminiExtractor(make_settings(), client=client).extract("a tender")

    assert result.input_tokens == 1000
    assert result.output_tokens == 1000, "candidates + thoughts"


async def test_the_schema_is_sent_as_response_json_schema():
    """`response_schema=<PydanticModel>` raises PydanticSerializationError in
    this SDK, and the shape it accepts cannot express the optional fields.
    Locking the working spelling in so a future tidy-up does not restore the
    obvious-looking broken one."""
    client = _FakeClient(_Response(json.dumps(VALID_PAYLOAD)))
    await GeminiExtractor(make_settings(), client=client).extract("a tender")

    config = client.calls[0]["config"]
    assert config.response_json_schema is not None
    assert config.response_schema is None
    assert config.response_mime_type == "application/json"


# --- configuration ----------------------------------------------------------


async def test_missing_gemini_key_names_the_right_variable():
    extractor = GeminiExtractor(make_settings(gemini_api_key="", anthropic_api_key="present"))
    with pytest.raises(ExtractionNotConfigured) as err:
        _ = extractor.client
    assert "GEMINI_API_KEY" in str(err.value)


async def test_the_factory_honours_the_configured_provider():
    assert isinstance(get_extractor(make_settings()), GeminiExtractor)
    anthropic = get_extractor(
        Settings(extraction_provider="anthropic", anthropic_api_key="k", dry_run=True)
    )
    assert isinstance(anthropic, TenderExtractor)
    assert not isinstance(anthropic, GeminiExtractor)


class _Model:
    def __init__(self, name):
        self.name = name


class _ListClient:
    def __init__(self, *names):
        self._names = names
        self.aio = self

    @property
    def models(self):
        return self

    async def list(self):
        return [_Model(n) for n in self._names]


async def test_startup_rejects_a_model_id_the_key_cannot_use():
    with pytest.raises(ExtractionNotConfigured) as err:
        await verify_gemini_model(
            make_settings(), client=_ListClient("models/gemini-2.5-flash", "models/gemini-2.5-pro")
        )
    # The message must list what IS available -- otherwise the operator is
    # told "wrong" with no way to be right.
    assert "gemini-2.5-flash" in str(err.value)


async def test_startup_accepts_the_model_by_bare_name():
    """ListModels returns "models/x"; the generate call takes either form."""
    await verify_gemini_model(make_settings(), client=_ListClient("models/gemini-3.6-flash"))


async def test_a_failed_verification_call_does_not_block_startup():
    """A timed-out metadata call is not evidence the model is wrong.

    Refusing to start over it would trade one outage for another.
    """

    class _Broken:
        aio = None

        def __init__(self):
            self.aio = self

        @property
        def models(self):
            return self

        async def list(self):
            raise TimeoutError("network")

    await verify_gemini_model(make_settings(), client=_Broken())


# --- embeddings -------------------------------------------------------------
#
# Measured against the live API before these were written: gemini-embedding-001
# returns exactly 1024 values when asked for them (so the vector(1024) column
# needs no migration), and a truncated vector comes back with L2 norm ~0.62
# rather than 1.0. Cosine (`<=>`, the only operator 0005_matching_engine.sql
# uses) is immune to that. Inner product is not, and it is the obvious future
# optimization -- against un-normalized vectors it would scale every score
# down by ~0.62, push real matches under the 0.72 default min_score, and
# produce zero alerts, which reads exactly like a quiet week in procurement.


class _Embedding:
    def __init__(self, values):
        self.values = values


class _EmbedResponse:
    def __init__(self, vectors):
        self.embeddings = [_Embedding(v) for v in vectors]


class _EmbedClient:
    def __init__(self, *responses):
        self._responses = list(responses)
        self.calls = []
        self.aio = self

    @property
    def models(self):
        return self

    async def embed_content(self, **kwargs):
        self.calls.append(kwargs)
        return self._responses.pop(0)


def _unnormalised(dim=1024, scale=0.62):
    """A vector with L2 norm != 1, like a truncated Gemini embedding."""
    import math

    raw = [1.0] * dim
    n = math.sqrt(sum(x * x for x in raw))
    return [x / n * scale for x in raw]


async def test_truncated_vectors_are_normalised_before_storage():
    import math

    from app.nlp.embeddings import GeminiEmbeddingProvider

    client = _EmbedClient(_EmbedResponse([_unnormalised()]))
    provider = GeminiEmbeddingProvider(make_settings(embedding_provider="gemini"), client=client)

    (vector,) = await provider.embed(["a tender"])

    norm = math.sqrt(sum(x * x for x in vector))
    assert abs(norm - 1.0) < 1e-6, f"stored vector has norm {norm}, not unit length"


async def test_the_requested_width_is_the_schema_width():
    """Asking for the native width and truncating later would be a migration."""
    from app.nlp.embeddings import GeminiEmbeddingProvider

    client = _EmbedClient(_EmbedResponse([_unnormalised()]))
    settings = make_settings(embedding_provider="gemini")
    await GeminiEmbeddingProvider(settings, client=client).embed(["a tender"])

    assert client.calls[0]["config"].output_dimensionality == settings.embedding_dim == 1024


async def test_documents_and_queries_use_matching_task_types():
    """Mixing them embeds into two different spaces, and the similarity
    between them is noise wearing a score."""
    from app.nlp.embeddings import GeminiEmbeddingProvider

    client = _EmbedClient(_EmbedResponse([_unnormalised()]), _EmbedResponse([_unnormalised()]))
    provider = GeminiEmbeddingProvider(make_settings(embedding_provider="gemini"), client=client)

    await provider.embed(["a tender"], input_type="document")
    await provider.embed(["radios"], input_type="query")

    assert client.calls[0]["config"].task_type == "RETRIEVAL_DOCUMENT"
    assert client.calls[1]["config"].task_type == "RETRIEVAL_QUERY"


async def test_a_wrong_width_is_refused_rather_than_inserted():
    from app.nlp.embeddings import GeminiEmbeddingProvider

    client = _EmbedClient(_EmbedResponse([[0.1] * 768]))
    provider = GeminiEmbeddingProvider(make_settings(embedding_provider="gemini"), client=client)

    with pytest.raises(ValueError, match="768 dimensions"):
        await provider.embed(["a tender"])


async def test_a_zero_vector_does_not_become_NaN():
    """Dividing by a zero norm would put NaNs in a vector column, and the
    INSERT would then fail describing the column instead of the empty input."""
    from app.nlp.embeddings import GeminiEmbeddingProvider

    client = _EmbedClient(_EmbedResponse([[0.0] * 1024]))
    provider = GeminiEmbeddingProvider(make_settings(embedding_provider="gemini"), client=client)

    (vector,) = await provider.embed(["a tender"])
    assert all(x == 0.0 for x in vector)


# --- quota exhaustion -------------------------------------------------------
#
# Measured, not imagined: a real sweep marked 22 of 22 tenders `failed` with
# `429 RESOURCE_EXHAUSTED`, including three that had extracted successfully an
# hour earlier -- the upsert wrote the transient error over good data. The
# quota turned out to be
# `GenerateRequestsPerDayPerProjectPerModel-FreeTier, limit: 20`: per DAY, not
# per minute. The `retryDelay: 5s` the same error carries is misleading at
# that scale, so the quotaId is what gets read.

_PER_DAY_429 = (
    "ClientError: 429 RESOURCE_EXHAUSTED. {'error': {'code': 429, "
    "'message': 'You exceeded your current quota', 'details': [{'@type': "
    "'type.googleapis.com/google.rpc.QuotaFailure', 'violations': [{'quotaId': "
    "'GenerateRequestsPerDayPerProjectPerModel-FreeTier', 'quotaValue': '20'}]}, "
    "{'@type': 'type.googleapis.com/google.rpc.RetryInfo', 'retryDelay': '5s'}]}}"
)
_PER_MINUTE_429 = (
    "ClientError: 429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'details': "
    "[{'quotaId': 'GenerateRequestsPerMinutePerProjectPerModel-FreeTier'}, "
    "{'@type': 'type.googleapis.com/google.rpc.RetryInfo', 'retryDelay': '0.01s'}]}}"
)


class _RaisingClient:
    """Raises the queued errors, then replays the queued responses."""

    def __init__(self, *items):
        self._items = list(items)
        self.calls = 0
        self.aio = self

    @property
    def models(self):
        return self

    async def generate_content(self, **kwargs):
        self.calls += 1
        item = self._items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


async def test_a_per_day_quota_aborts_instead_of_burning_the_run():
    from app.nlp.extractor import ExtractionQuotaExhausted

    client = _RaisingClient(RuntimeError(_PER_DAY_429))
    with pytest.raises(ExtractionQuotaExhausted) as err:
        await GeminiExtractor(make_settings(), client=client).extract("a tender")

    assert client.calls == 1, "a per-day quota must not be retried inside the run"
    assert "GEMINI_MODEL" in str(err.value), "the message must say how to get unstuck"


async def test_a_per_minute_quota_waits_and_succeeds():
    """The other half: a short window IS worth waiting out."""
    client = _RaisingClient(RuntimeError(_PER_MINUTE_429), _Response(json.dumps(VALID_PAYLOAD)))
    result = await GeminiExtractor(make_settings(), client=client).extract("a tender")

    assert result.status == "extracted"
    assert client.calls == 2


async def test_the_pipeline_stops_the_loop_instead_of_failing_every_remaining_row(monkeypatch):
    """The defect this exists to prevent.

    Without the break, one exhausted quota walks the rest of the tender list
    marking each `failed` -- and the upsert writes that over extractions that
    already succeeded.
    """
    from app.nlp.extractor import ExtractionQuotaExhausted
    from app.pipeline import IngestionPipeline

    seen = []

    class _Stub:
        def __init__(self, i):
            self.external_id = f"t{i}"
            self.source_url = "https://example.invalid"
            self.title = "t"

    class _Agent:
        key = "fake"
        display_name = "fake"
        base_url = "https://example.invalid"
        country = "IL"

        async def discover(self, limit=25):
            return [_Stub(i) for i in range(10)]

        async def fetch(self, stub):
            seen.append(stub.external_id)
            raise ExtractionQuotaExhausted("quota gone; set GEMINI_MODEL to one with quota")

    monkeypatch.setattr("app.pipeline.get_agents", lambda *a, **k: [_Agent()])

    # Same reason as the sibling test in test_api_surface: the loop this
    # asserts on only runs when ingestion extracts.
    pipeline = IngestionPipeline(
        settings=make_settings(extraction_provider="gemini", extract_on_ingest=True)
    )
    (report,) = await pipeline.run(["fake"], limit=10)

    assert len(seen) == 1, f"stopped after {len(seen)} tenders, not the whole list of 10"
    assert report.found == 10
    assert any("aborted" in e for e in report.errors)


def test_selecting_anthropic_does_not_load_the_google_sdk():
    """`app/nlp/gemini.py` imports google.genai lazily so an Anthropic-only
    deployment never needs it installed. A top-level import in pipeline.py
    would quietly undo that, and nothing else would notice."""
    import subprocess
    import sys

    code = (
        "import app.pipeline, sys; "
        "print('LOADED' if 'google.genai' in sys.modules else 'NOT_LOADED')"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert "NOT_LOADED" in out.stdout, out.stdout + out.stderr
