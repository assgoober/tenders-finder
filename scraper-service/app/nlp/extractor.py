"""Schema-constrained tender extraction.

Contract (REPOMIX.md ADR-2): nothing reaches the database unvalidated.

    1. Constrained decode against the Pydantic schema.
    2. On validation failure, one repair attempt with the errors fed back.
    3. On a second failure, flag for human review. Never persist a partial
       or coerced payload.

The structural guarantee comes from the API's structured outputs, not from
asking the model nicely for JSON in the prompt.
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

    @property
    def ok(self) -> bool:
        return self.status == "extracted" and self.extraction is not None


class TenderExtractor:
    """Turns raw tender text into a validated `TenderExtraction`."""

    def __init__(self, settings: Settings | None = None, client=None) -> None:
        self.settings = settings or get_settings()
        self._client = client

    @property
    def client(self):
        # Imported lazily so the module (and the tests that mock the client)
        # load without the SDK installed or an API key present.
        if self._client is None:
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
            except Exception as exc:  # noqa: BLE001 - surfaced on the result
                logger.exception("extraction call failed on attempt %d", attempt)
                return ExtractionResult(
                    status="failed",
                    error=f"{type(exc).__name__}: {exc}",
                    model=self.settings.anthropic_model,
                    attempts=attempt,
                    truncated_input=truncated,
                )

            usage = self._usage(response)
            parsed, error = self._validate(response)

            if parsed is not None:
                return ExtractionResult(
                    status="extracted",
                    extraction=parsed,
                    model=self.settings.anthropic_model,
                    attempts=attempt,
                    input_tokens=usage[0],
                    output_tokens=usage[1],
                    truncated_input=truncated,
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
            model=self.settings.anthropic_model,
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
        return await self.client.messages.parse(
            model=self.settings.anthropic_model,
            max_tokens=self.settings.anthropic_max_tokens,
            system=[
                {
                    "type": "text",
                    "text": EXTRACTION_SYSTEM_PROMPT,
                    # The system prompt is byte-stable across every document,
                    # so it caches cleanly. The document itself follows the
                    # breakpoint and is never cached.
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            thinking={"type": "adaptive"},
            messages=messages,
            output_format=TenderExtraction,
        )

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
