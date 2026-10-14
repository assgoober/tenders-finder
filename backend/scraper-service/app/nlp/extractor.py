"""Schema-constrained tender extraction.

Contract (REPOMIX.md ADR-2): nothing reaches the database unvalidated.

    1. Constrained decode against the Pydantic schema.
    2. On validation failure, one repair attempt with the errors fed back.
    3. On a second failure, flag for human review. Never persist a partial
       or coerced payload.

The structural guarantee comes from the API's structured outputs, not from
asking the model nicely for JSON in the prompt.

## Refusals

A safety classifier can decline a request. That is not an exception — it is
a successful HTTP 200 carrying `stop_reason: "refusal"` and an empty
`content` — so code that reads the response without checking first sees an
empty answer and misreports it as "the model returned no structured output".

This matters more here than in most applications. Tender documents describe
munitions, sensors, secure comms and dual-use components; that is precisely
the material adjacent to what the classifiers are built to decline, so false
positives are expected rather than exotic. Two responses:

* `fallbacks` (opt-in, on by default) re-runs a declined request on
  Anthropic's recommended substitute model server-side, inside the same
  call. A false positive is recovered rather than surfaced.
* If the whole chain still declines, the tender is routed to human review
  with the refusal category recorded — never retried. The same prompt over
  the same document will be declined again, so a retry only spends money.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from pydantic import ValidationError

from ..config import Settings, get_settings
from ..schemas.tender import TenderExtraction
from .prompts import (
    EXTRACTION_SYSTEM_PROMPT,
    PROMPT_VERSION,
    REPAIR_INSTRUCTION,
    build_extraction_user_message,
)

logger = logging.getLogger(__name__)

# Beyond this, we are paying to re-read boilerplate annexes. Truncation is a
# real information loss, so it is reported on the result rather than done
# silently; the long-document fix is chunked extraction, not a bigger window.
MAX_DOCUMENT_CHARS = 400_000

# Gates the scalar `fallbacks: "default"` form. The array form
# (`fallbacks=[{"model": ...}]`) is gated by a different header, and pairing
# either header with the other form is a 400 — so these two travel together.
FALLBACK_BETA = "server-side-fallback-2026-07-01"


@dataclass(slots=True)
class ExtractionResult:
    """Outcome of one extraction attempt."""

    status: Literal["extracted", "needs_review", "failed"]
    extraction: TenderExtraction | None = None
    error: str | None = None
    model: str | None = None
    prompt_version: str = PROMPT_VERSION
    attempts: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    truncated_input: bool = False
    #: Policy category when the request was declined, e.g. "cyber". Set only
    #: on a refusal. Deliberately not a separate `status`: the outcome is
    #: still "a human must read this one", and the four database statuses
    #: stay as they are. This says *why*, so a run full of policy declines is
    #: distinguishable from a run full of unparseable scans.
    refusal_category: str | None = None
    #: Which model actually answered. Differs from `model` when a fallback
    #: rescued a declined request.
    served_by: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "extracted" and self.extraction is not None

    @property
    def refused(self) -> bool:
        return self.refusal_category is not None


class ExtractionQuotaExhausted(RuntimeError):
    """The provider's request quota is gone for the rest of the period.

    Lives here rather than beside the backend that raises it, because the
    pipeline has to catch it and importing it from `gemini` would load the
    Google SDK on every deployment -- including Anthropic-only ones, which
    need never have it installed.

    Distinct from an ordinary failure because the correct response differs. A
    429 is not a property of the document: retrying on the next tender
    produces the same 429, and the loop then marks every remaining row
    `failed`, overwriting good extractions with a transient error. One
    aborted run reporting "quota exhausted after N tenders" is strictly more
    useful than 169 identical per-tender failures.
    """


class ExtractionNotConfigured(RuntimeError):
    """Raised when extraction is attempted with no ANTHROPIC_API_KEY set.

    Without this, the SDK is handed `api_key=None` and refuses at the point
    it assembles request headers:

        TypeError: "Could not resolve authentication method. Expected one of
        api_key, auth_token, or credentials to be set..."

    That message is accurate about the SDK and useless about the cause. It
    arrives once per tender, so a sweep of a portal produces one identical
    header error per row and reads like a transport bug — sending whoever
    investigates into the client library rather than into the environment.
    Same failure class as ADR-13's refusal-reported-as-empty-output, and the
    same fix: say what actually happened.
    """


class TenderExtractor:
    """Turns raw tender text into a validated `TenderExtraction`."""

    #: Human-readable provider label, used in log lines and error messages.
    provider_name = "anthropic"

    def __init__(self, settings: Settings | None = None, client=None) -> None:
        self.settings = settings or get_settings()
        self._client = client

    @property
    def model_name(self) -> str:
        """The model recorded on every ExtractionResult.

        A property rather than a literal so the validate-repair-review ladder
        below stays provider-agnostic — subclasses swap the transport, not
        the ladder.
        """
        return self.settings.anthropic_model

    @property
    def client(self):
        # Imported lazily so the module (and the tests that mock the client)
        # load without the SDK installed or an API key present.
        if self._client is None:
            if not self.settings.has_anthropic:
                raise ExtractionNotConfigured(
                    "ANTHROPIC_API_KEY is not set, so tender text cannot be extracted. "
                    "Set it in the service's .env. DRY_RUN does not skip extraction — "
                    "it only skips the database write."
                )
            from anthropic import AsyncAnthropic

            self._client = AsyncAnthropic(
                api_key=self.settings.anthropic_api_key,
                timeout=self.settings.extraction_timeout_seconds,
            )
        return self._client

    async def extract(
        self,
        raw_text: str,
        *,
        source_name: str | None = None,
        source_url: str | None = None,
    ) -> ExtractionResult:
        if not raw_text or not raw_text.strip():
            return ExtractionResult(status="failed", error="empty document text")

        truncated = len(raw_text) > MAX_DOCUMENT_CHARS
        if truncated:
            logger.warning(
                "document exceeds %d chars (%d); truncating for extraction",
                MAX_DOCUMENT_CHARS,
                len(raw_text),
            )
            raw_text = raw_text[:MAX_DOCUMENT_CHARS]

        messages: list[dict] = [
            {
                "role": "user",
                "content": build_extraction_user_message(
                    raw_text, source_name=source_name, source_url=source_url
                ),
            }
        ]

        last_error: str | None = None
        usage = (None, None)

        # Attempt 1 is the constrained decode. Attempt 2 is the repair round —
        # the model sees the exact validation errors, not a bare "try again".
        for attempt in (1, 2):
            try:
                response = await self._call(messages)
            except (ExtractionQuotaExhausted, ExtractionNotConfigured):
                # Both mean "this run cannot proceed", not "this document
                # failed". Turning them into a `failed` result is what the
                # broad clause below would do, and that is precisely the
                # defect: the caller would keep going, fail every remaining
                # tender the same way, and overwrite each one's stored
                # extraction with the transient error. They belong to the run,
                # so they leave by the run's path.
                raise
            except Exception as exc:  # noqa: BLE001 - surfaced on the result
                logger.exception("extraction call failed on attempt %d", attempt)
                return ExtractionResult(
                    status="failed",
                    error=f"{type(exc).__name__}: {exc}",
                    model=self.model_name,
                    attempts=attempt,
                    truncated_input=truncated,
                )

            usage = self._usage(response)

            # Before anything reads `content`. A pre-output decline carries an
            # empty content array, so every check below would otherwise
            # describe a policy refusal as a malformed answer.
            category = self._refusal_category(response)
            if category is not None:
                logger.warning(
                    "extraction declined by policy (category=%s) on attempt %d — routing to "
                    "human review without retrying; the same prompt would be declined again",
                    category,
                    attempt,
                )
                return ExtractionResult(
                    status="needs_review",
                    error=f"request declined by policy (category={category})",
                    model=self.model_name,
                    attempts=attempt,
                    input_tokens=usage[0],
                    output_tokens=usage[1],
                    truncated_input=truncated,
                    refusal_category=category,
                    served_by=getattr(response, "model", None),
                )

            parsed, error = self._validate(response)

            if parsed is not None:
                return ExtractionResult(
                    status="extracted",
                    extraction=parsed,
                    model=self.model_name,
                    attempts=attempt,
                    input_tokens=usage[0],
                    output_tokens=usage[1],
                    truncated_input=truncated,
                    served_by=getattr(response, "model", None),
                )

            last_error = error
            logger.warning("extraction validation failed (attempt %d): %s", attempt, error)

            if attempt == 1:
                # Echo the model's own output back before the correction so
                # the repair turn edits a concrete draft.
                messages.append({"role": "assistant", "content": self._raw_text(response)})
                messages.append(
                    {"role": "user", "content": REPAIR_INSTRUCTION.format(errors=error)}
                )

        # Two constrained attempts failed. A human reads this one.
        return ExtractionResult(
            status="needs_review",
            error=last_error,
            model=self.model_name,
            attempts=2,
            input_tokens=usage[0],
            output_tokens=usage[1],
            truncated_input=truncated,
        )

    async def _call(self, messages: list[dict]):
        """One constrained-decode request.

        `messages.parse` with `output_format` constrains decoding to the
        schema, so malformed JSON is not a failure mode we have to handle —
        only semantically wrong values are.

        Adaptive thinking is on: reading a 60-page solicitation and deciding
        which clauses are threshold conditions is exactly the kind of work it
        helps with. Effort defaults to `high`.
        """
        request = {
            "model": self.settings.anthropic_model,
            "max_tokens": self.settings.anthropic_max_tokens,
            "system": [
                {
                    "type": "text",
                    "text": EXTRACTION_SYSTEM_PROMPT,
                    # The system prompt is byte-stable across every document,
                    # so it caches cleanly. The document itself follows the
                    # breakpoint and is never cached.
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            "thinking": {"type": "adaptive"},
            "messages": messages,
            "output_format": TenderExtraction,
        }

        if not self.settings.anthropic_fallbacks:
            return await self.client.messages.parse(**request)

        # `fallbacks` is a beta parameter, so this goes through the beta
        # namespace. "default" lets the API route by refusal category rather
        # than pinning a substitute model here — a pinned one becomes a
        # migration the day it is retired.
        try:
            return await self.client.beta.messages.parse(
                **request,
                betas=[FALLBACK_BETA],
                fallbacks="default",
            )
        except TypeError as exc:
            # An installed SDK too old to know `fallbacks` would otherwise
            # break extraction outright. Losing the fallback is a degradation;
            # losing extraction is an outage. Loud, and not silently retried
            # on every subsequent document — the operator should upgrade.
            logger.error(
                "this anthropic SDK does not accept `fallbacks` (%s); extracting without "
                "refusal fallbacks — `pip install -U 'anthropic>=1.2.0'`, or set "
                "ANTHROPIC_FALLBACKS=false to silence this",
                exc,
            )
            return await self.client.messages.parse(**request)

    @staticmethod
    def _refusal_category(response) -> str | None:
        """The policy category if the request was declined, else None.

        Branches on `stop_reason`, never on `stop_details`: the details
        object is informational and is absent on some refusals, so treating
        its presence as the signal would miss them.
        """
        if getattr(response, "stop_reason", None) != "refusal":
            return None

        details = getattr(response, "stop_details", None)
        category = getattr(details, "category", None)
        if category is None and isinstance(details, dict):
            category = details.get("category")

        # A refusal with no category is still a refusal. Returning None here
        # would report it as a successful empty answer.
        return category or "unspecified"

    @staticmethod
    def _validate(response) -> tuple[TenderExtraction | None, str | None]:
        """Pull the validated model off the response.

        The SDK validates for us; this re-validates defensively so that a
        mocked or future-changed client cannot slip an unvalidated dict
        through into the database.
        """
        parsed = getattr(response, "parsed_output", None)

        if isinstance(parsed, TenderExtraction):
            return parsed, None

        if parsed is None:
            return None, "model returned no structured output"

        try:
            return TenderExtraction.model_validate(parsed), None
        except ValidationError as exc:
            return None, exc.json(indent=2)

    @staticmethod
    def _raw_text(response) -> str:
        for block in getattr(response, "content", []) or []:
            if getattr(block, "type", None) == "text":
                return block.text
        return ""

    @staticmethod
    def _usage(response) -> tuple[int | None, int | None]:
        usage = getattr(response, "usage", None)
        if usage is None:
            return None, None
        return getattr(usage, "input_tokens", None), getattr(usage, "output_tokens", None)


def get_extractor(settings: Settings | None = None, client=None) -> TenderExtractor:
    """The extractor for the configured provider.

    The import is local so `extractor` does not import `gemini`, which
    imports `extractor` -- and so a deployment that never selects Gemini
    never needs the Google SDK installed.
    """
    settings = settings or get_settings()

    if settings.extraction_provider == "gemini":
        from .gemini import GeminiExtractor

        return GeminiExtractor(settings, client=client)

    return TenderExtractor(settings, client=client)
