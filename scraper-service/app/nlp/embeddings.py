"""Embedding providers.

Anthropic does not serve an embeddings endpoint, so the vector half of the
matching engine uses a separate provider. Voyage AI is the default; the
protocol below keeps that choice swappable (REPOMIX.md ADR-6).

Two things matter for correctness:

  * Dimension must match the `vector(N)` column exactly. A mismatch is a
    hard database error, so it is checked here rather than at INSERT time.
  * Query and document vectors must come from the same provider *and* the
    same model. Cosine similarity across two embedding spaces is noise that
    looks like a score.
"""

from __future__ import annotations

import hashlib
import logging
import math
from typing import Protocol, runtime_checkable

from ..config import Settings, get_settings

logger = logging.getLogger(__name__)

# Voyage distinguishes the two: a corpus document and a search query get
# different prefixes and embed into comparable but not identical positions.
InputType = str  # "document" | "query"


@runtime_checkable
class EmbeddingProvider(Protocol):
    model_name: str
    dimension: int

    async def embed(self, texts: list[str], *, input_type: InputType = "document") -> list[list[float]]:
        ...


class VoyageEmbeddingProvider:
    """Production provider, backed by the Voyage AI REST API."""

    ENDPOINT = "https://api.voyageai.com/v1/embeddings"
    # Voyage caps batch size; larger lists are chunked.
    MAX_BATCH = 96

    def __init__(self, settings: Settings) -> None:
        if not settings.voyage_api_key:
            raise ValueError(
                "VOYAGE_API_KEY is required for the 'voyage' embedding provider. "
                "Set EMBEDDING_PROVIDER=hash for offline development."
            )
        self._api_key = settings.voyage_api_key
        self.model_name = settings.voyage_model
        self.dimension = settings.embedding_dim

    async def embed(
        self, texts: list[str], *, input_type: InputType = "document"
    ) -> list[list[float]]:
        import httpx

        vectors: list[list[float]] = []
        async with httpx.AsyncClient(timeout=60.0) as http:
            for start in range(0, len(texts), self.MAX_BATCH):
                batch = texts[start : start + self.MAX_BATCH]
                response = await http.post(
                    self.ENDPOINT,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json={
                        "input": batch,
                        "model": self.model_name,
                        "input_type": input_type,
                        "output_dimension": self.dimension,
                    },
                )
                response.raise_for_status()
                payload = response.json()
                # Voyage does not guarantee response order matches input order.
                ordered = sorted(payload["data"], key=lambda d: d["index"])
                vectors.extend(item["embedding"] for item in ordered)

        _assert_one_vector_per_text(vectors, texts, self.model_name)
        _assert_dimensions(vectors, self.dimension)
        return vectors


class GeminiEmbeddingProvider:
    """Google provider, for deployments already using Gemini for extraction.

    Exists so that choosing Gemini does not also mean signing up for a third
    vendor just to embed. It is the same swappable-provider point ADR-6
    describes; nothing else in the pipeline changes.

    Two measured facts shape this class, both confirmed against the live API
    rather than read off a page:

    **1024 dimensions are reachable, so no migration is needed.**
    `gemini-embedding-001` accepts `output_dimensionality`, and 1024 -- the
    width `supabase/migrations/0001_extensions.sql` declares -- comes back
    exactly. A provider that could only emit its native width would have
    forced `ALTER TABLE ... TYPE vector(N)` plus a full re-embedding.

    **A truncated vector is not normalized, and this class normalizes it.**
    At the native 3072 the returned vector has L2 norm 1.0; asked for 1024 it
    comes back at ~0.62, because Matryoshka truncation drops magnitude with
    the tail. Today that is harmless: every operator in
    `0005_matching_engine.sql` is `<=>`, pgvector's cosine distance, which
    normalizes internally -- so `min_score` thresholds still mean what they
    say. It is harmless *one layer away*, though. Switching to `<#>` (inner
    product) is the ordinary optimization once vectors are known-normalized,
    and against un-normalized vectors it would scale every score down by ~0.62
    -- pushing real matches under the 0.72 default threshold and producing no
    alerts at all. Zero alerts is indistinguishable from a quiet week in
    procurement, which is the failure this project keeps having to design
    against. Normalizing here costs one pass over 1024 floats and removes the
    trap, and it matches what Voyage already returns.
    """

    #: The API accepts a list and returns embeddings in input order (checked:
    #: an element of a batch equals the same text embedded alone). Chunked
    #: anyway, so one oversized run cannot become one oversized request.
    MAX_BATCH = 96

    def __init__(self, settings: Settings, client=None) -> None:
        if not settings.gemini_api_key:
            raise ValueError(
                "GEMINI_API_KEY is required for the 'gemini' embedding provider. "
                "Set EMBEDDING_PROVIDER=hash for offline development."
            )
        self._settings = settings
        self._client = client
        self.model_name = settings.gemini_embedding_model
        self.dimension = settings.embedding_dim

    @property
    def client(self):
        if self._client is None:
            from google import genai

            self._client = genai.Client(api_key=self._settings.gemini_api_key)
        return self._client

    #: How long to wait out a per-minute rate limit, and how many times.
    #: The free tier's embedding cap is
    #: `EmbedContentRequestsPerMinutePerUserPerProjectPerModel`, which is a
    #: RATE, not a budget: the window refills on its own. Treating it as
    #: exhaustion -- which is what an unhandled 429 amounts to -- throws away
    #: a corpus we are seconds away from being allowed to embed. The first
    #: wide sweep ended that way after 213 of 6,000 tenders.
    RATE_LIMIT_WAIT_SECONDS = 62.0
    RATE_LIMIT_RETRIES = 6

    #: Total seconds a single embed() call may spend waiting on quota before
    #: it gives up on the whole run.
    #:
    #: The per-message branch below cannot be trusted on its own. The API
    #: names the quota it enforced only sometimes: the detailed body says
    #: `quotaId: EmbedContentRequestsPerDayPerUserPerProjectPerModel-FreeTier`,
    #: and the generic one says "You exceeded your current quota" and nothing
    #: else. A daily cap arriving in the generic dress is indistinguishable
    #: from a per-minute one, and a sweep spent fifteen waits of sixty-two
    #: seconds against a wall that was never going to fall that day.
    #:
    #: So the budget is the backstop, and it rests on something that is true
    #: regardless of what the message says: a limit that has not cleared in
    #: five minutes is not a per-minute limit. Stop, and say so.
    QUOTA_WAIT_BUDGET_SECONDS = 300.0

    @staticmethod
    def _stated_retry_delay(message: str) -> float | None:
        """Seconds the API asked for, read out of its own message.

        It says so in two places and neither is a header:
        `"Please retry in 12.034577317s"` in the prose, and `retryDelay` in
        the structured detail block. Reading it is the difference between
        waiting twelve seconds and waiting a minute -- and, when the real
        wait is longer, between one honest failure and six scheduled ones.
        """
        import re

        for pattern in (r"retry in ([0-9.]+)s", r"retryDelay['\"]?[:=]\s*['\"]?([0-9.]+)"):
            found = re.search(pattern, message)
            if not found:
                continue
            try:
                return max(1.0, min(float(found.group(1)), 120.0))
            except ValueError:
                continue
        return None

    async def _embed_batch_with_retry(self, batch, task_type, types):
        """One batch, waiting out a per-minute cap rather than dying on it.

        Deliberately NOT retried on anything else. A malformed request, a bad
        key, a model that does not exist -- those return the same 429-shaped
        confidence to a broad `except`, and sleeping a minute between six
        identical failures helps nobody.
        """
        import asyncio

        last: Exception | None = None
        waited = 0.0
        for attempt in range(1, self.RATE_LIMIT_RETRIES + 1):
            try:
                return await self.client.aio.models.embed_content(
                    model=self.model_name,
                    contents=batch,
                    config=types.EmbedContentConfig(
                        output_dimensionality=self.dimension,
                        task_type=task_type,
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                message = str(exc)
                exhausted = "RESOURCE_EXHAUSTED" in message or "429" in message
                # Keyed on the ONE thing we can actually read. The API does
                # not reliably name the quota it enforced: sometimes the body
                # carries `quotaId:
                # EmbedContentRequestsPerMinutePerUserPerProjectPerModel-FreeTier`
                # and sometimes only "You exceeded your current quota". An
                # earlier version required "PerMinute" to be present, which
                # meant the retry never fired on the generic form -- and the
                # test for it passed, because the test asserted the message
                # shape I had assumed rather than the one the API sends.
                #
                # So: wait it out unless the body says PerDay. Guessing wrong
                # toward waiting costs a few minutes; guessing wrong toward
                # raising costs the corpus.
                retryable = exhausted and "PerDay" not in message
                if not retryable or attempt == self.RATE_LIMIT_RETRIES:
                    raise
                last = exc
                # The server states how long it wants: "Please retry in
                # 12.034577317s". Believing it beats a fixed minute in both
                # directions -- it is usually shorter, and when it is longer
                # a fixed minute just fails again on schedule.
                wait = self._stated_retry_delay(message)
                if wait is None:
                    wait = self.RATE_LIMIT_WAIT_SECONDS

                if waited + wait > self.QUOTA_WAIT_BUDGET_SECONDS:
                    logger.error(
                        "embedding quota has not cleared in %.0fs; this is a "
                        "daily cap, not a rate limit -- stopping rather than "
                        "waiting out the day",
                        waited,
                    )
                    raise
                logger.warning(
                    "embedding quota (attempt %d/%d); waiting %.0fs%s",
                    attempt, self.RATE_LIMIT_RETRIES, wait,
                    " (server-stated)" if self._stated_retry_delay(message) else "",
                )
                await asyncio.sleep(wait)
                waited += wait
        raise last  # unreachable; kept so the contract is explicit

    async def embed(
        self, texts: list[str], *, input_type: InputType = "document"
    ) -> list[list[float]]:
        from google.genai import types

        # The corpus and the query must be embedded with matching task types,
        # or the two vectors land in comparable-looking but different spaces
        # and the similarity is noise wearing a score.
        task_type = "RETRIEVAL_QUERY" if input_type == "query" else "RETRIEVAL_DOCUMENT"

        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.MAX_BATCH):
            batch = texts[start : start + self.MAX_BATCH]
            response = await self._embed_batch_with_retry(batch, task_type, types)
            returned = list(response.embeddings)
            # Per BATCH, not only at the end: a provider that collapses ten
            # inputs into one would otherwise be caught only after the whole
            # call, with no way to say which batch went wrong.
            _assert_one_vector_per_text(returned, batch, self.model_name)
            vectors.extend(_normalize(list(e.values)) for e in returned)

        _assert_one_vector_per_text(vectors, texts, self.model_name)
        _assert_dimensions(vectors, self.dimension)
        return vectors


class HashEmbeddingProvider:
    """Deterministic local provider for development and tests.

    Produces stable, normalized vectors with no network call, so the whole
    pipeline — including the pgvector insert and the matching trigger — can
    be exercised without API keys. It captures lexical overlap only; it is
    not a semantic model and must never be used in production.
    """

    def __init__(self, settings: Settings) -> None:
        self.model_name = "hash-dev"
        self.dimension = settings.embedding_dim

    async def embed(
        self, texts: list[str], *, input_type: InputType = "document"
    ) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        tokens = [t for t in text.lower().split() if t]

        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimension
            # Sign from a different byte so tokens don't all push one way.
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[index] += sign

        norm = math.sqrt(sum(v * v for v in vector))
        if norm == 0:
            # Cosine distance is undefined against a zero vector; anchor it.
            vector[0] = 1.0
            return vector
        return [v / norm for v in vector]


def _normalize(vector: list[float]) -> list[float]:
    """Scale to unit length. A zero vector is returned unchanged.

    Dividing by a zero norm would produce NaNs, and a row of NaNs in a vector
    column fails at INSERT with an error about the column rather than about
    the empty input that caused it.
    """
    norm = math.sqrt(sum(x * x for x in vector))
    if norm == 0:
        return vector
    return [x / norm for x in vector]


def _assert_dimensions(vectors: list[list[float]], expected: int) -> None:
    for vector in vectors:
        if len(vector) != expected:
            raise ValueError(
                f"embedding provider returned {len(vector)} dimensions, "
                f"but the schema declares vector({expected}). "
                "Changing provider requires a migration and a full re-embedding."
            )


def _assert_one_vector_per_text(vectors: list, texts: list, model: str) -> None:
    """As many vectors back as texts sent, in the same order.

    This checked nothing until a model was found that violates it.
    `gemini-embedding-2` accepts a list of ten texts and returns ONE vector,
    with no error and correct dimensions -- so `_assert_dimensions` passed it
    happily. The caller then does `zip(stubs, texts, vectors)`, and zip stops
    at the shortest: ninety-five tenders would be dropped without a single
    counter disagreeing, and the one that survived could be carrying a vector
    computed from all ninety-six texts at once.

    A batch endpoint that quietly returns fewer results than it was given is
    the worst shape a bug can take here, because the corpus would look full
    and match wrongly. Dimensions were being checked because they are easy to
    check; this is the invariant that actually matters.
    """
    if len(vectors) != len(texts):
        raise ValueError(
            f"{model} returned {len(vectors)} embeddings for {len(texts)} "
            "texts. A provider that does not return one vector per input "
            "cannot be batched: the vectors would be silently misaligned "
            "with the rows they are written to."
        )


def get_embedding_provider(settings: Settings | None = None) -> EmbeddingProvider:
    settings = settings or get_settings()
    provider = settings.embedding_provider.lower()

    if provider == "voyage":
        return VoyageEmbeddingProvider(settings)
    if provider == "gemini":
        return GeminiEmbeddingProvider(settings)
    if provider == "hash":
        logger.warning("using the hash embedding provider — development only, not semantic")
        return HashEmbeddingProvider(settings)

    raise ValueError(f"unknown embedding provider: {settings.embedding_provider!r}")
