# -*- coding: utf-8 -*-
"""The backoff has to see a rate limit to be a backoff.

`with_backoff` retried on raised exceptions. A rate-limited call does not
raise: it returns a perfectly valid response object carrying 429, so the
try/except caught nothing, the caller raised on the status afterwards --
outside the retry -- and the attempt budget was never touched.

The first wide sweep failed all thirty-one shards in eighteen seconds, and
every layer above reported it correctly. The honesty was not the problem.
"""
from __future__ import annotations

import pytest

from app.agents.base import TenderSourceAgent
from app.config import Settings


class _Response:
    def __init__(self, status_code, headers=None):
        self.status_code = status_code
        self.headers = headers or {}


class _Agent(TenderSourceAgent):
    key = "probe"
    display_name = "probe"
    base_url = "https://example.invalid"

    async def discover(self, *, limit: int = 25):
        return []

    async def fetch(self, stub):
        raise NotImplementedError


def _agent(**over):
    base = dict(extraction_provider="gemini", gemini_api_key="k",
                embedding_provider="hash",
                scrape_max_retries=3,
                scrape_backoff_base_seconds=0.001,
                scrape_backoff_max_seconds=0.002)
    base.update(over)
    return _Agent(Settings(**base))


@pytest.mark.asyncio
async def test_a_429_is_retried_not_returned():
    agent = _agent()
    calls = []

    async def op():
        calls.append(1)
        return _Response(429) if len(calls) < 3 else _Response(200)

    result = await agent.with_backoff(op, description="search")

    assert result.status_code == 200
    assert len(calls) == 3, "the rate limit was not retried"


@pytest.mark.asyncio
async def test_a_400_is_returned_immediately():
    """A bad query is not a rate limit. Retrying it wastes the budget and
    delays the error message the API was willing to give us."""
    agent = _agent()
    calls = []

    async def op():
        calls.append(1)
        return _Response(400)

    result = await agent.with_backoff(op)

    assert result.status_code == 400
    assert len(calls) == 1, "a client error should not be retried"


@pytest.mark.asyncio
async def test_a_persistent_429_is_returned_so_the_caller_can_report_it():
    """After the budget is spent the response comes back rather than being
    swallowed: the caller raises with the body, which is how the shard shows
    up by name in the partial-harvest warning."""
    agent = _agent(scrape_max_retries=2)
    calls = []

    async def op():
        calls.append(1)
        return _Response(429)

    result = await agent.with_backoff(op)

    assert result.status_code == 429
    assert len(calls) == 3, "one initial attempt plus two retries"


@pytest.mark.asyncio
async def test_retry_after_is_honoured_over_our_own_exponent(monkeypatch):
    """The server knows how long it wants to be left alone better than an
    exponent does."""
    agent = _agent()
    slept = []

    async def _sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr("app.agents.base.asyncio.sleep", _sleep)
    calls = []

    async def op():
        calls.append(1)
        return _Response(429, {"Retry-After": "7"}) if len(calls) == 1 else _Response(200)

    await agent.with_backoff(op)
    assert slept == [7.0]


@pytest.mark.asyncio
async def test_an_absurd_retry_after_is_capped(monkeypatch):
    """A server asking for an hour is asking us to stop, not to sleep
    through the run. Cap it and let the shard fail honestly."""
    agent = _agent()
    slept = []

    async def _sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr("app.agents.base.asyncio.sleep", _sleep)
    calls = []

    async def op():
        calls.append(1)
        return _Response(429, {"Retry-After": "3600"}) if len(calls) == 1 else _Response(200)

    await agent.with_backoff(op)
    assert slept == [120.0]


@pytest.mark.asyncio
async def test_a_malformed_retry_after_falls_back_to_the_exponent(monkeypatch):
    """The HTTP-date form is legal. Guessing at one we cannot parse is worse
    than using our own number."""
    agent = _agent()
    slept = []

    async def _sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr("app.agents.base.asyncio.sleep", _sleep)
    calls = []

    async def op():
        calls.append(1)
        return (_Response(429, {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"})
                if len(calls) == 1 else _Response(200))

    await agent.with_backoff(op)
    assert slept and slept[0] >= 1.0, "fell through without waiting at all"


@pytest.mark.asyncio
async def test_a_raised_exception_is_still_retried():
    """The original behaviour, kept: a portal says "slow down" in two
    different ways and both have to work."""
    agent = _agent()
    calls = []

    async def op():
        calls.append(1)
        if len(calls) < 2:
            raise ConnectionResetError("connection reset by peer")
        return _Response(200)

    result = await agent.with_backoff(op)
    assert result.status_code == 200
    assert len(calls) == 2


# --- the embedding rate limit is a rate, not a budget ----------------------

@pytest.mark.asyncio
async def test_a_per_minute_embedding_limit_is_waited_out(monkeypatch):
    """`EmbedContentRequestsPerMinutePerUserPerProjectPerModel` refills on
    its own. Treating it as exhaustion throws away a corpus we are seconds
    away from being allowed to embed -- which is how the first wide sweep
    ended, at 213 of 6,000."""
    from app.nlp.embeddings import GeminiEmbeddingProvider

    provider = GeminiEmbeddingProvider.__new__(GeminiEmbeddingProvider)
    provider.model_name = "gemini-embedding-001"
    provider.dimension = 1024
    provider.RATE_LIMIT_WAIT_SECONDS = 0.001

    calls = []

    class _Models:
        async def embed_content(self, **kw):
            calls.append(kw)
            if len(calls) < 3:
                raise RuntimeError(
                    "429 RESOURCE_EXHAUSTED quotaId: "
                    "EmbedContentRequestsPerMinutePerUserPerProjectPerModel-FreeTier"
                )
            return "embeddings"

    class _Aio:
        models = _Models()

    class _Client:
        aio = _Aio()

    provider._client = _Client()

    class _Types:
        @staticmethod
        def EmbedContentConfig(**kw):
            return kw

    result = await provider._embed_batch_with_retry(["a"], "RETRIEVAL_DOCUMENT", _Types)

    assert result == "embeddings"
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_a_non_rate_limit_error_is_raised_immediately():
    """A bad key or a missing model returns the same 429-shaped confidence
    to a broad except. Sleeping a minute between six identical failures
    helps nobody and hides the real message."""
    from app.nlp.embeddings import GeminiEmbeddingProvider

    provider = GeminiEmbeddingProvider.__new__(GeminiEmbeddingProvider)
    provider.model_name = "m"
    provider.dimension = 1024
    provider.RATE_LIMIT_WAIT_SECONDS = 0.001

    calls = []

    class _Models:
        async def embed_content(self, **kw):
            calls.append(1)
            raise RuntimeError("400 INVALID_ARGUMENT: unknown model")

    class _Aio:
        models = _Models()

    class _Client:
        aio = _Aio()

    provider._client = _Client()

    class _Types:
        @staticmethod
        def EmbedContentConfig(**kw):
            return kw

    with pytest.raises(RuntimeError, match="INVALID_ARGUMENT"):
        await provider._embed_batch_with_retry(["a"], "RETRIEVAL_DOCUMENT", _Types)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_a_daily_quota_is_not_waited_out():
    """A per-DAY cap does not refill in sixty seconds. Sleeping through six
    of them wastes six minutes to arrive at the same failure."""
    from app.nlp.embeddings import GeminiEmbeddingProvider

    provider = GeminiEmbeddingProvider.__new__(GeminiEmbeddingProvider)
    provider.model_name = "m"
    provider.dimension = 1024
    provider.RATE_LIMIT_WAIT_SECONDS = 0.001

    calls = []

    class _Models:
        async def embed_content(self, **kw):
            calls.append(1)
            raise RuntimeError(
                "429 RESOURCE_EXHAUSTED quotaId: "
                "EmbedContentRequestsPerDayPerProject-FreeTier"
            )

    class _Aio:
        models = _Models()

    class _Client:
        aio = _Aio()

    provider._client = _Client()

    class _Types:
        @staticmethod
        def EmbedContentConfig(**kw):
            return kw

    with pytest.raises(RuntimeError, match="PerDay"):
        await provider._embed_batch_with_retry(["a"], "RETRIEVAL_DOCUMENT", _Types)
    assert len(calls) == 1, "a daily cap was retried as if it were a rate"
