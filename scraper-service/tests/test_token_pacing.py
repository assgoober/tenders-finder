# -*- coding: utf-8 -*-
"""Pace the embedder by tokens, because tokens are what runs out.

The account's rate-limit dashboard, read after a sweep wrote nothing:

    Gemini Embedding 1   RPM 106/100   TPM 37.57K/30K   RPD 965/1K

Three limits, and the one we had reasoned about -- requests -- was the least
binding once batching existed. Measured over 1,001 real listing rows: 792
characters each, roughly 198 tokens. 30,000 TPM divided by that is about 150
tenders a minute, and no batch size changes it.
"""
from __future__ import annotations

import pytest

from app.config import Settings
from app.nlp.embeddings import GeminiEmbeddingProvider, estimate_tokens


def provider(**over) -> GeminiEmbeddingProvider:
    p = GeminiEmbeddingProvider.__new__(GeminiEmbeddingProvider)
    p.model_name = "gemini-embedding-001"
    p.dimension = 1024
    p.MAX_BATCH = 96
    p._tpm = over.get("tpm", 27000)
    p._max_request_tokens = over.get("max_request_tokens", 18000)
    p._spent = []
    return p


# --- estimating ------------------------------------------------------------

def test_the_estimate_is_deliberately_heavy_for_a_multilingual_corpus():
    """Four characters per token is the English rule of thumb. Greek, Hebrew
    and Bulgarian tokenize to MORE tokens per character, and those are
    exactly the notices this corpus exists to carry -- so a /4 estimate runs
    light precisely where being light is expensive."""
    assert estimate_tokens(["x" * 792]) == 264      # /3, not /4
    assert estimate_tokens(["x" * 792]) > 792 // 4


def test_an_empty_batch_still_costs_something():
    """Zero would let an unbounded number of empty requests through the
    pacer without ever advancing the window."""
    assert estimate_tokens([]) == 1
    assert estimate_tokens([""]) == 1


# --- chunking by tokens, not only by count --------------------------------

def test_average_listings_are_cut_by_tokens_before_they_hit_the_count():
    """At 264 tokens each, 96 would be 25,000 in one request."""
    chunks = list(provider()._chunks(["x" * 792] * 200))
    assert all(len(c) < 96 for c in chunks), [len(c) for c in chunks]
    assert all(estimate_tokens(c) <= 18000 for c in chunks)
    assert sum(len(c) for c in chunks) == 200, "a chunk was dropped"


def test_long_listings_make_much_smaller_batches():
    """The p95 listing is 2,590 characters. Ninety-six of those is 62,000
    tokens in a single request -- twice the entire minute's budget, spent at
    once, in a batch that looks identical from the outside."""
    chunks = list(provider()._chunks(["y" * 2590] * 100))
    assert max(len(c) for c in chunks) <= 25, [len(c) for c in chunks]
    assert max(estimate_tokens(c) for c in chunks) <= 18000


def test_a_single_oversized_text_is_not_silently_dropped():
    """One listing bigger than the whole per-request budget still has to be
    sent. Refusing it here would make the tender invisible; the API's own
    limit is the right place for that to fail, loudly."""
    chunks = list(provider()._chunks(["z" * 200000]))
    assert len(chunks) == 1
    assert chunks[0] == ["z" * 200000]


def test_nothing_is_lost_or_duplicated_across_chunks():
    texts = [f"listing number {i} " + "x" * (100 * (i % 30)) for i in range(300)]
    rebuilt = [t for chunk in provider()._chunks(texts) for t in chunk]
    assert rebuilt == texts


# --- the pacer -------------------------------------------------------------

@pytest.mark.asyncio
async def test_requests_inside_the_budget_do_not_wait():
    p = provider(tpm=27000)
    slept = []
    import asyncio
    real = asyncio.sleep
    asyncio.sleep = lambda s: slept.append(s) or real(0)
    try:
        for _ in range(5):
            await p._wait_for_token_budget(5000)
    finally:
        asyncio.sleep = real
    assert slept == [], "waited despite 25,000 of a 27,000 budget"


@pytest.mark.asyncio
async def test_the_request_that_would_exceed_the_budget_waits():
    p = provider(tpm=27000)
    slept = []
    import asyncio
    real = asyncio.sleep

    async def _fake(seconds):
        slept.append(seconds)
        # Pretend the window rolled, so the loop can make progress.
        p._spent = []

    asyncio.sleep = _fake
    try:
        await p._wait_for_token_budget(20000)
        await p._wait_for_token_budget(20000)   # 40,000 > 27,000
    finally:
        asyncio.sleep = real

    assert slept, "the second request went straight through the ceiling"
    assert slept[0] <= 60.0


@pytest.mark.asyncio
async def test_a_request_larger_than_the_whole_budget_is_not_a_deadlock():
    """One request bigger than the per-minute allowance can never fit. The
    pacer must send it on an empty window rather than sleep forever waiting
    for room that will not appear."""
    p = provider(tpm=1000)
    import asyncio
    real = asyncio.sleep
    calls = []

    async def _fake(seconds):
        calls.append(seconds)
        if len(calls) > 3:
            raise AssertionError("pacer is spinning on an impossible request")
        await real(0)

    asyncio.sleep = _fake
    try:
        await p._wait_for_token_budget(50000)
    finally:
        asyncio.sleep = real
    assert calls == []


@pytest.mark.asyncio
async def test_the_window_slides_rather_than_resetting():
    """The limit is per minute as the server measures it, not per minute
    from whenever our run started."""
    import time
    p = provider(tpm=27000)
    p._spent = [(time.monotonic() - 61.0, 26000)]   # a minute ago; expired
    import asyncio
    real = asyncio.sleep
    slept = []
    asyncio.sleep = lambda s: slept.append(s) or real(0)
    try:
        await p._wait_for_token_budget(20000)
    finally:
        asyncio.sleep = real
    assert slept == [], "an expired entry still counted against the budget"
