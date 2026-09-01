"""Google Gemini extraction backend.

A second implementation of the transport underneath the extraction ladder.
Everything that makes extraction trustworthy stays where it already was:
`TenderExtractor.extract` still runs the constrained decode, the single
repair round against the real validation errors, and the route to
`needs_review` on a second failure (ADR-2). This module only answers four
provider-shaped questions -- how to call, how to spot a refusal, how to read
the output, how to count tokens -- so the two backends cannot drift apart in
the part that matters.

Three things behave differently on Gemini, and none of them is cosmetic.

**Schema transport.** `response_schema=<PydanticModel>` -- the obvious
spelling, and the one most examples show -- raises
`PydanticSerializationError` in this SDK, and the older `response_schema`
path cannot express `anyOf`/`$ref` anyway. `TenderExtraction` has 17 fields,
most of them optional, so its JSON Schema is full of both. We send
`response_json_schema` with the schema Pydantic generates, which carries the
whole shape.

**Refusals arrive twice, in two different places.** ADR-13's insight was that
a policy decline is a successful response, not an exception, and that reading
`content` before checking for it reports "the model returned no structured
output" -- sending someone to hunt a schema bug that does not exist. Gemini
has the same trap with two doors: `prompt_feedback.block_reason` when the
*prompt* is blocked before generation, and `candidates[0].finish_reason` when
generation is stopped part-way. A blocked prompt comes back HTTP 200 with no
candidates at all, so `response.text` is empty or raises. Both are checked
before any read of the output.

**There is no server-side fallback.** ADR-13's second half -- `fallbacks:
"default"`, which re-runs a declined request on a substitute model inside the
same call -- has no Gemini equivalent. A decline here is terminal: it goes
straight to `needs_review` with its category recorded, and nothing rescues
it. Tender documents describe munitions, sensors and secure comms, so
declines are expected rather than exotic; on this backend they cost a tender
each. That is a real capability loss and is recorded as such in REPOMIX.md
ADR-20.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re

from pydantic import ValidationError

from ..config import Settings
from ..schemas.tender import TenderExtraction
from .extractor import (
    ExtractionNotConfigured,
    ExtractionQuotaExhausted,
    TenderExtractor,
)
from .prompts import EXTRACTION_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

#: Schema for the constrained decode. Computed once: it is a pure function of
#: the model class, and rebuilding it per document would be the kind of
#: per-request variation that defeats any future prompt caching.
_RESPONSE_JSON_SCHEMA = TenderExtraction.model_json_schema()

#: `finish_reason` values that mean the model was stopped by policy rather
#: than finishing its answer. Deliberately a denylist of known-blocking
#: reasons rather than "anything that is not STOP": MAX_TOKENS and
#: FINISH_REASON_UNSPECIFIED are ordinary outcomes, and mislabelling a
#: truncated answer as a policy decline would put it in the wrong half of the
#: review queue (ADR-15) -- the half that says "no retry will ever help".
_BLOCKING_FINISH_REASONS = frozenset(
    {
        "SAFETY",
        "PROHIBITED_CONTENT",
        "BLOCKLIST",
        "SPII",
        "RECITATION",
        "IMAGE_SAFETY",
    }
)


#: Per-minute limits are worth waiting out inside a run; per-day limits are
#: not. The quotaId distinguishes them, and it is read rather than inferred
#: from the retryDelay, which reports seconds in both cases.
_PER_DAY_QUOTA = re.compile(r"PerDay", re.I)
_RETRY_DELAY = re.compile(r"'retryDelay':\s*'(\d+(?:\.\d+)?)s'")

#: Bounded: two waits, then give up on this document rather than stalling a
#: whole sweep behind one row.
_RATE_LIMIT_RETRIES = 2


class GeminiExtractor(TenderExtractor):
    """`TenderExtractor` with Google Gemini underneath.

    Subclasses rather than reimplements: the ladder in the base class is the
    ADR-2 guarantee, and there must be exactly one copy of it.
    """

    provider_name = "gemini"

    @property
    def model_name(self) -> str:
        return self.settings.gemini_model

    @property
    def client(self):
        if self._client is None:
            if not self.settings.has_gemini:
                raise ExtractionNotConfigured(
                    "EXTRACTION_PROVIDER is 'gemini' but GEMINI_API_KEY is not set. "
                    "Set it in the service's .env. An ANTHROPIC_API_KEY does not "
                    "satisfy this -- the provider selects which key is required."
                )
            # Imported lazily so the module loads, and the tests that inject a
            # fake client run, without the SDK installed.
            from google import genai

            self._client = genai.Client(api_key=self.settings.gemini_api_key)
        return self._client

    # --- transport -----------------------------------------------------------

    async def _call(self, messages: list[dict]):
        """One constrained-decode request.

        The base class speaks Anthropic's `{role, content}` shape because that
        is what it was written against; translating here keeps the ladder from
        having to know there are two dialects. Gemini names the assistant turn
        `model`, and the system prompt is a config field rather than a message.
        """
        from google.genai import types

        contents = [
            types.Content(
                role="model" if m["role"] == "assistant" else "user",
                parts=[types.Part(text=m["content"])],
            )
            for m in messages
        ]

        config = types.GenerateContentConfig(
            system_instruction=EXTRACTION_SYSTEM_PROMPT,
            response_mime_type="application/json",
            # NOT `response_schema=TenderExtraction` -- see the module
            # docstring. That spelling raises PydanticSerializationError, and
            # the shape it accepts cannot carry the optional fields anyway.
            response_json_schema=_RESPONSE_JSON_SCHEMA,
            max_output_tokens=self.settings.gemini_max_output_tokens,
        )

        for attempt in range(_RATE_LIMIT_RETRIES + 1):
            try:
                return await self.client.aio.models.generate_content(
                    model=self.model_name,
                    contents=contents,
                    config=config,
                )
            except Exception as exc:  # noqa: BLE001 - re-raised or converted below
                message = str(exc)
                if "429" not in message and "RESOURCE_EXHAUSTED" not in message:
                    raise

                if _PER_DAY_QUOTA.search(message):
                    raise ExtractionQuotaExhausted(
                        f"{self.model_name}: the provider's per-day request quota is "
                        "exhausted, so every remaining tender in this run would fail the "
                        "same way and overwrite whatever is already stored. Aborting the "
                        "run. Set GEMINI_MODEL to a model with quota left, or enable "
                        "billing."
                    ) from exc

                if attempt == _RATE_LIMIT_RETRIES:
                    raise

                found = _RETRY_DELAY.search(message)
                delay = min(float(found.group(1)) if found else 5.0, 30.0)
                logger.warning(
                    "rate limited by %s; waiting %.1fs before retry %d/%d",
                    self.model_name, delay, attempt + 1, _RATE_LIMIT_RETRIES,
                )
                await asyncio.sleep(delay)

        raise AssertionError("unreachable")

    # --- response adapters ---------------------------------------------------

    @staticmethod
    def _refusal_category(response) -> str | None:
        """Policy decline, checked before anything reads the output.

        Two doors, in the order they can occur. A blocked prompt never
        produces a candidate, so checking `finish_reason` first would raise on
        an empty list and lose the category.
        """
        feedback = getattr(response, "prompt_feedback", None)
        block_reason = getattr(feedback, "block_reason", None)
        if block_reason is not None:
            return str(getattr(block_reason, "value", block_reason)).lower()

        candidates = getattr(response, "candidates", None) or []
        if not candidates:
            # No candidate and no stated block reason. Something stopped this
            # response, the API did not say what, and it is not a schema
            # problem -- naming it as one is the exact misreport ADR-13 exists
            # to prevent.
            return "unspecified"

        finish = getattr(candidates[0], "finish_reason", None)
        if finish is None:
            return None
        name = str(getattr(finish, "value", finish)).upper()
        return name.lower() if name in _BLOCKING_FINISH_REASONS else None

    @staticmethod
    def _validate(response) -> tuple[TenderExtraction | None, str | None]:
        """Validate the output against the Pydantic model.

        `response.parsed` is only populated on the `response_schema` path,
        which is not the one we can use, so the JSON is parsed and validated
        here. That is the stronger arrangement for ADR-2 anyway: our own
        Pydantic model is the enforcement rather than a second opinion on the
        SDK's, and nothing unvalidated has a route to the database.
        """
        text = GeminiExtractor._raw_text(response)
        if not text.strip():
            return None, "model returned no structured output"

        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            # Constrained decoding should make this unreachable. If it is
            # reached, the constraint did not hold, and that is worth saying
            # plainly rather than reporting as a validation failure.
            return None, f"constrained decode returned non-JSON: {exc}"

        try:
            return TenderExtraction.model_validate(payload), None
        except ValidationError as exc:
            # Fed verbatim into the repair turn, so the model edits against
            # the real errors rather than a summary of them.
            return None, exc.json(indent=2)

    @staticmethod
    def _raw_text(response) -> str:
        """The response text, or "" -- never an exception.

        `response.text` is a property that walks candidates and parts, and it
        raises or warns when there are none. It is called on the refusal path
        too, so it has to be total.
        """
        try:
            text = response.text
        except Exception:  # noqa: BLE001 - a missing candidate is not an error here
            return ""
        return text or ""

    @staticmethod
    def _usage(response) -> tuple[int | None, int | None]:
        """Prompt and output tokens.

        `thoughts_token_count` is added to the output side: thinking tokens
        are generated and billed as output, and leaving them out would make
        the cost of a run read lower than the invoice.
        """
        usage = getattr(response, "usage_metadata", None)
        if usage is None:
            return None, None

        prompt = getattr(usage, "prompt_token_count", None)
        answer = getattr(usage, "candidates_token_count", None)
        thoughts = getattr(usage, "thoughts_token_count", None)

        if answer is None and thoughts is None:
            return prompt, None
        return prompt, (answer or 0) + (thoughts or 0)


async def verify_gemini_model(settings: Settings, client=None) -> None:
    """Confirm the configured model id exists, at startup.

    The id is configuration and nobody has typed it against a live account
    yet. Without this check a wrong name is discovered one tender at a time,
    ~190 times, as an opaque per-document failure. With it, the service
    refuses to start and says which ids the key can actually see.

    Raises `ExtractionNotConfigured` -- the same class as a missing key,
    because it is the same kind of problem: the pipeline is not configured to
    run, and no amount of retrying changes that.
    """
    extractor = GeminiExtractor(settings, client=client)
    try:
        page = await extractor.client.aio.models.list()
        available = [m.name for m in page if getattr(m, "name", None)]
    except Exception as exc:  # noqa: BLE001
        # A network or auth failure is not proof the model is wrong, so this
        # warns rather than blocking startup. Refusing to start because a
        # metadata call timed out would trade one outage for another.
        logger.warning(
            "could not verify GEMINI_MODEL=%s against the API (%s: %s); continuing",
            settings.gemini_model,
            type(exc).__name__,
            exc,
        )
        return

    wanted = settings.gemini_model
    # ListModels returns fully-qualified names ("models/gemini-x"); the
    # generate call takes either form, so compare on the last segment.
    if any(name.split("/")[-1] == wanted.split("/")[-1] for name in available):
        logger.info("GEMINI_MODEL=%s verified against the API", wanted)
        return

    raise ExtractionNotConfigured(
        f"GEMINI_MODEL={wanted!r} is not a model this API key can use. "
        f"Available: {', '.join(sorted(n.split('/')[-1] for n in available)) or '(none returned)'}. "
        "Set GEMINI_MODEL to one of those, or GEMINI_VERIFY_MODEL=false to skip this check."
    )
