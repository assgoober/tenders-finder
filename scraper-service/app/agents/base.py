"""Aggregation agent contract.

Every portal is a `TenderSourceAgent`. The interface deliberately splits the
two things that break independently (REPOMIX.md ADR-5):

    discover()  — navigation. Find the tender listings. Breaks when the
                  portal changes its search UI, pagination, or auth.
    fetch()     — extraction. Read one tender into raw text. Breaks when the
                  portal changes its detail page or document format.

A portal redesign usually breaks exactly one of them. Keeping them apart
means a broken `discover()` still leaves a working `fetch()` for tenders
already queued, and the failure is legible in `scrape_runs`.

Agents return raw text. They never call the LLM, never touch the database,
and never decide what a field means — that is the pipeline's job.
"""

from __future__ import annotations

import abc
import asyncio
import logging
import random
from dataclasses import dataclass, field
from datetime import datetime

from ..config import Settings

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class TenderStub:
    """A tender spotted during discovery, before its document is fetched."""

    external_id: str
    title: str
    source_url: str
    buyer_name: str | None = None
    publication_date: datetime | None = None
    deadline: datetime | None = None
    # Anything the listing page exposed that the detail page might not.
    listing_metadata: dict = field(default_factory=dict)

    def listing_text(self) -> str:
        """What the buyer published, in the buyer's own words.

        This is what a tender is embedded from before anyone has paid to
        extract it. It is not a summary and not a model's reading of one --
        it is the notice's own title and description, which is why it can be
        trusted into the matching corpus without a validation ladder in
        front of it.

        Quality varies by portal and that variance is the point: TED
        listings carry a real description, so their vectors are rich. The
        Israeli MoD list publishes a 27-character title and nothing else, so
        its vectors are thin -- exactly as thin as they already were, since
        that portal never had a document to extract in the first place.
        Nothing here makes any tender worse than it is today.
        """
        meta = self.listing_metadata or {}
        parts: list[str] = [self.title]

        for key in ("description", "scope", "summary", "listing_title"):
            value = meta.get(key)
            # `listing_title` repeats `title` on most portals; keeping it
            # twice would weight the title against the description rather
            # than adding anything.
            if isinstance(value, str) and value.strip() and value.strip() != self.title.strip():
                parts.append(value.strip())

        if self.buyer_name:
            parts.append(self.buyer_name)

        # CPV codes are a controlled vocabulary, so they carry real signal
        # across languages -- 35613000 means "unmanned aerial vehicles" to a
        # Greek notice and a Danish one alike.
        codes = meta.get("cpv_codes")
        if isinstance(codes, (list, tuple)) and codes:
            parts.append(" ".join(str(c) for c in codes if c))

        return "\n".join(p for p in parts if p).strip()


@dataclass(slots=True)
class TenderDocument:
    """An attachment belonging to a tender."""

    file_name: str
    file_url: str
    mime_type: str | None = None
    text_content: str | None = None


@dataclass(slots=True)
class FetchedTender:
    """A tender with its full text, ready for extraction."""

    stub: TenderStub
    raw_text: str
    documents: list[TenderDocument] = field(default_factory=list)

    def combined_text(self) -> str:
        """Page text plus every attachment's text, in one document.

        Threshold conditions frequently live in an annex rather than the
        notice page, so extraction must see both.
        """
        parts = [self.raw_text]
        for doc in self.documents:
            if doc.text_content:
                parts.append(f"\n\n--- Attachment: {doc.file_name} ---\n{doc.text_content}")
        return "\n".join(parts)


class TenderSourceAgent(abc.ABC):
    """Base class for a single portal."""

    #: Stable identifier. Must match `tender_sources.key` in the database.
    key: str = ""
    display_name: str = ""
    base_url: str = ""
    country: str | None = None
    sector: str = "defense"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        if not self.key:
            raise ValueError(f"{type(self).__name__} must define a non-empty `key`")

    # --- Navigation ---------------------------------------------------------

    @abc.abstractmethod
    async def discover(self, *, limit: int = 50) -> list[TenderStub]:
        """List tenders currently published on this portal."""

    # --- Extraction ---------------------------------------------------------

    @abc.abstractmethod
    async def fetch(self, stub: TenderStub) -> FetchedTender:
        """Retrieve the full text and attachments for one tender."""

    # --- Shared plumbing ----------------------------------------------------

    async def with_backoff(self, operation, *, description: str = "request"):
        """Retry `operation` with exponential backoff and full jitter.

        Government portals rate-limit and IP-ban aggressively, and a ban
        costs far more than a slow run. Jitter matters because agents run
        concurrently: without it, retries from several agents re-collide in
        lockstep and look exactly like the traffic that triggers a ban.
        """
        attempt = 0
        while True:
            try:
                return await operation()
            except Exception as exc:  # noqa: BLE001 - re-raised once retries are spent
                attempt += 1
                if attempt > self.settings.scrape_max_retries:
                    logger.error(
                        "[%s] %s failed after %d attempts: %s",
                        self.key,
                        description,
                        attempt,
                        exc,
                    )
                    raise

                ceiling = min(
                    self.settings.scrape_backoff_base_seconds * (2 ** (attempt - 1)),
                    self.settings.scrape_backoff_max_seconds,
                )
                delay = random.uniform(0, ceiling)
                logger.warning(
                    "[%s] %s failed (attempt %d/%d): %s — retrying in %.1fs",
                    self.key,
                    description,
                    attempt,
                    self.settings.scrape_max_retries,
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)
