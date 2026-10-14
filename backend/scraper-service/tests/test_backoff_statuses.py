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


@pytest.mark.asyncio
async def test_the_generic_quota_message_is_also_waited_out():
    """The exact body a live sweep received, pasted rather than imagined.

    The first version of this retry required the string "PerMinute" to be in
    the message. The API does not reliably send a quotaId at all, so the
    retry never fired -- and the test covering it passed, because the test
    asserted the payload I had assumed. This one is the real thing.
    """
    from app.nlp.embeddings import GeminiEmbeddingProvider

    provider = GeminiEmbeddingProvider.__new__(GeminiEmbeddingProvider)
    provider.model_name = "gemini-embedding-001"
    provider.dimension = 1024
    provider.RATE_LIMIT_WAIT_SECONDS = 0.001

    REAL = ("429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': "
            "'You exceeded your current quota, please check your plan and "
            "billing details. For more information on this error, head to: "
            "https://ai.google.dev/gemini-api/docs/rate-limits. ', "
            "'status': 'RESOURCE_EXHAUSTED'}}")

    calls = []

    class _Models:
        async def embed_content(self, **kw):
            calls.append(1)
            if len(calls) < 2:
                raise RuntimeError(REAL)
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
    assert len(calls) == 2, "the generic quota message was not retried"


def test_the_stated_retry_delay_is_read_from_the_message():
    """The API says how long it wants, twice, and neither is a header.

    Real body from a live sweep:
        "... Please retry in 12.034577317s."
    plus `retryDelay` in the structured detail block.
    """
    from app.nlp.embeddings import GeminiEmbeddingProvider as G

    assert G._stated_retry_delay("Please retry in 12.034577317s.") == 12.034577317
    assert G._stated_retry_delay("{'retryDelay': '38s'}") == 38.0
    assert G._stated_retry_delay("You exceeded your current quota") is None


def test_an_absurd_stated_delay_is_capped():
    from app.nlp.embeddings import GeminiEmbeddingProvider as G

    assert G._stated_retry_delay("Please retry in 86400s.") == 120.0


def test_a_sub_second_stated_delay_still_waits_a_second():
    """A quota answered in 80ms is the request that caused it."""
    from app.nlp.embeddings import GeminiEmbeddingProvider as G

    assert G._stated_retry_delay("Please retry in 0.2s") == 1.0


@pytest.mark.asyncio
async def test_the_daily_cap_named_in_a_real_body_is_not_waited_out():
    """The body that ended the third sweep, pasted rather than imagined:

      Quota exceeded for metric:
      generativelanguage.googleapis.com/embed_content_free_tier_requests,
      limit: 1000
      quotaId: EmbedContentRequestsPerDayPerUserPerProject

    One thousand embedding REQUESTS per day. Two unbatched sweeps spent
    213 and 788 of them -- 1,001 against a limit of 1,000, to the request --
    so the batched sweep that followed had nothing left to spend and
    embedded nothing at all. Waiting six minutes for a day to end is not a
    retry strategy.
    """
    from app.nlp.embeddings import GeminiEmbeddingProvider

    provider = GeminiEmbeddingProvider.__new__(GeminiEmbeddingProvider)
    provider.model_name = "gemini-embedding-001"
    provider.dimension = 1024
    provider.RATE_LIMIT_WAIT_SECONDS = 0.001

    REAL = ("429 RESOURCE_EXHAUSTED. Quota exceeded for metric: "
            "generativelanguage.googleapis.com/embed_content_free_tier_requests, "
            "limit: 1000. Please retry in 12.03s. "
            "quotaId: EmbedContentRequestsPerDayPerUserPerProject")

    calls = []

    class _Models:
        async def embed_content(self, **kw):
            calls.append(1)
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

    with pytest.raises(RuntimeError, match="PerDay"):
        await provider._embed_batch_with_retry(["a"], "RETRIEVAL_DOCUMENT", _Types)
    assert len(calls) == 1, "a daily cap was retried as if it were a rate"


@pytest.mark.asyncio
async def test_a_quota_that_never_clears_stops_instead_of_waiting_out_the_day():
    """The backstop for a message that will not say what it is.

    The API names its quota only sometimes. A daily cap wearing the generic
    "You exceeded your current quota" is indistinguishable from a per-minute
    one, and a sweep spent fifteen waits of sixty-two seconds against a wall
    that was not going to fall that day -- writing nothing at all.

    This rests on something true regardless of the wording: a limit that has
    not cleared in five minutes is not a per-minute limit.
    """
    from app.nlp.embeddings import GeminiEmbeddingProvider

    provider = GeminiEmbeddingProvider.__new__(GeminiEmbeddingProvider)
    provider.model_name = "gemini-embedding-001"
    provider.dimension = 1024
    provider.RATE_LIMIT_WAIT_SECONDS = 62.0
    provider.RATE_LIMIT_RETRIES = 50          # generous on purpose
    provider.QUOTA_WAIT_BUDGET_SECONDS = 300.0

    slept = []
    calls = []

    class _Models:
        async def embed_content(self, **kw):
            calls.append(1)
            raise RuntimeError("429 RESOURCE_EXHAUSTED. You exceeded your current quota.")

    class _Aio:
        models = _Models()

    class _Client:
        aio = _Aio()

    provider._client = _Client()

    class _Types:
        @staticmethod
        def EmbedContentConfig(**kw):
            return kw

    import app.nlp.embeddings as mod

    async def _sleep(seconds):
        slept.append(seconds)

    real = __import__("asyncio").sleep
    __import__("asyncio").sleep = _sleep
    try:
        with pytest.raises(RuntimeError):
            await provider._embed_batch_with_retry(["a"], "RETRIEVAL_DOCUMENT", _Types)
    finally:
        __import__("asyncio").sleep = real

    assert sum(slept) <= 300.0, f"waited {sum(slept)}s against a daily cap"
    assert len(calls) < 10, f"{len(calls)} attempts despite a five-minute budget"


# --- one vector per text, or nothing ---------------------------------------

def test_a_provider_that_collapses_a_batch_is_refused():
    """Found by trying `gemini-embedding-2`, which has an untouched daily
    budget and looked like the way out of an exhausted one.

    It accepts a list of ten texts and returns ONE vector. No error, correct
    dimensions -- so the dimension check passed it. The caller then does
    `zip(stubs, texts, vectors)`, and zip stops at the shortest: ninety-five
    tenders dropped without a counter disagreeing, and the survivor carrying
    a vector computed from all ninety-six texts at once.

    A corpus that looks full and matches wrongly is the worst outcome
    available here. Dimensions were checked because they are easy to check.
    """
    from app.nlp.embeddings import _assert_one_vector_per_text

    with pytest.raises(ValueError, match="returned 1 embeddings for 10 texts"):
        _assert_one_vector_per_text([[0.0] * 1024], ["t"] * 10, "gemini-embedding-2")


def test_matching_counts_pass():
    from app.nlp.embeddings import _assert_one_vector_per_text

    _assert_one_vector_per_text([[0.0], [0.1], [0.2]], ["a", "b", "c"], "m")


def test_the_dimension_check_alone_would_have_let_it_through():
    """The point of the test above, stated as the gap it closes."""
    from app.nlp.embeddings import _assert_dimensions

    # One perfectly-shaped vector, ten texts. Silent under the old check.
    _assert_dimensions([[0.0] * 1024], 1024)
