"""Ingestion pipeline: discover → fetch → extract → embed → persist.

Ordering here is deliberate. The embedding is written in the same row write
as the tender, because that write is what fires the matching trigger. A
tender that exists without an embedding is invisible to the alert engine —
so it must never be the persisted end state of a successful run.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime

from .agents import FetchedTender, TenderSourceAgent, get_agents
from .config import Settings, get_settings
from .nlp.embeddings import EmbeddingProvider, get_embedding_provider
from .nlp.extractor import ExtractionResult, TenderExtractor
from .storage.supabase_repo import SupabaseRepository

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class IngestReport:
    source_key: str
    found: int = 0
    ingested: int = 0
    needs_review: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "source": self.source_key,
            "found": self.found,
            "ingested": self.ingested,
            "needs_review": self.needs_review,
            "failed": self.failed,
            "errors": self.errors[:10],
        }


class IngestionPipeline:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        extractor: TenderExtractor | None = None,
        embedder: EmbeddingProvider | None = None,
        repository: SupabaseRepository | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.extractor = extractor or TenderExtractor(self.settings)
        self.embedder = embedder or get_embedding_provider(self.settings)
        self.repository = repository or SupabaseRepository(self.settings)

    async def run(
        self, source_keys: list[str] | None = None, *, limit: int = 25
    ) -> list[IngestReport]:
        agents = get_agents(source_keys, self.settings)
        # Bounded concurrency: several portals at once, but never enough
        # simultaneous traffic at one of them to look like an attack.
        semaphore = asyncio.Semaphore(self.settings.scrape_concurrency)

        async def guarded(agent: TenderSourceAgent) -> IngestReport:
            async with semaphore:
                return await self.run_source(agent, limit=limit)

        return list(await asyncio.gather(*(guarded(agent) for agent in agents)))

    async def run_source(self, agent: TenderSourceAgent, *, limit: int = 25) -> IngestReport:
        report = IngestReport(source_key=agent.key)
        run_id: str | None = None
        source_id: str | None = None

        if not self.settings.dry_run:
            source_id = self.repository.upsert_source(
                {
                    "key": agent.key,
                    "display_name": agent.display_name or agent.key,
                    "base_url": agent.base_url,
                    "country": agent.country,
                    "sector": agent.sector,
                }
            )
            run_id = self.repository.start_run(source_id)

        try:
            stubs = await agent.discover(limit=limit)
            report.found = len(stubs)
            logger.info("[%s] discovered %d tenders", agent.key, report.found)

            for stub in stubs:
                try:
                    fetched = await agent.fetch(stub)
                    await self._ingest_one(fetched, source_id, report)
                except Exception as exc:  # noqa: BLE001
                    # One bad tender must not abort the run — the remaining
                    # tenders in this portal are still worth ingesting.
                    report.failed += 1
                    report.errors.append(f"{stub.external_id}: {type(exc).__name__}: {exc}")
                    logger.exception("[%s] ingest failed for %s", agent.key, stub.external_id)

            if not self.settings.dry_run:
                self.repository.finish_run(
                    run_id,
                    items_found=report.found,
                    items_ingested=report.ingested,
                    ok=True,
                )
        except Exception as exc:  # noqa: BLE001
            report.errors.append(f"discover: {type(exc).__name__}: {exc}")
            logger.exception("[%s] discovery failed", agent.key)
            if not self.settings.dry_run:
                self.repository.finish_run(
                    run_id,
                    items_found=report.found,
                    items_ingested=report.ingested,
                    ok=False,
                    error=str(exc),
                )

        return report

    async def _ingest_one(
        self, fetched: FetchedTender, source_id: str | None, report: IngestReport
    ) -> None:
        raw_text = fetched.combined_text()
        result = await self.extractor.extract(
            raw_text,
            source_name=fetched.stub.title,
            source_url=fetched.stub.source_url,
        )

        row = self._build_row(fetched, result, source_id, raw_text)

        if result.ok:
            assert result.extraction is not None
            vectors = await self.embedder.embed(
                [result.extraction.embedding_text()], input_type="document"
            )
            row["embedding"] = vectors[0]
            report.ingested += 1
        elif result.status == "needs_review":
            # Deliberately unembedded: an unvalidated tender must not enter
            # the matching corpus and generate alerts a human never checked.
            report.needs_review += 1
        else:
            report.failed += 1
            if result.error:
                report.errors.append(f"{fetched.stub.external_id}: {result.error}")

        if self.settings.dry_run:
            logger.info(
                "[dry-run] %s → %s (%s)",
                fetched.stub.external_id,
                result.status,
                row.get("title"),
            )
            return

        tender_id = self.repository.upsert_tender(row)

        documents = [
            {
                "file_name": doc.file_name,
                "file_url": doc.file_url,
                "mime_type": doc.mime_type,
                "text_content": doc.text_content,
            }
            for doc in fetched.documents
        ]
        self.repository.attach_documents(tender_id, documents)

    def _build_row(
        self,
        fetched: FetchedTender,
        result: ExtractionResult,
        source_id: str | None,
        raw_text: str,
    ) -> dict:
        stub = fetched.stub
        extraction = result.extraction

        # Listing-page values are the fallback. Where extraction succeeded,
        # the document's own statement wins: the listing card is a summary
        # written by the portal, the document is the tender.
        title = extraction.title if extraction else stub.title
        deadline = (extraction.deadline if extraction else None) or stub.deadline
        publication = (extraction.publication_date if extraction else None) or stub.publication_date

        row: dict = {
            "source_id": source_id,
            "external_id": stub.external_id,
            "source_url": stub.source_url,
            "title": title,
            "buyer_name": (extraction.buyer_name if extraction else None) or stub.buyer_name,
            "summary": extraction.summary if extraction else None,
            "publication_date": _iso_date(publication),
            "deadline": _iso(deadline),
            "estimated_value": extraction.estimated_value if extraction else None,
            "currency": extraction.currency if extraction else None,
            "raw_text": raw_text,
            "extraction_status": result.status,
            "extraction_error": result.error,
            "extraction_model": result.model,
        }

        metadata = extraction.to_metadata() if extraction else {}
        metadata["extraction"] = {
            "prompt_version": result.prompt_version,
            "attempts": result.attempts,
            "truncated_input": result.truncated_input,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
        }
        metadata["listing"] = stub.listing_metadata
        row["metadata"] = metadata

        return row

    async def embed_pending_profiles(self, limit: int = 100) -> int:
        """Vectorize alert profiles created through the web app.

        The dashboard writes a profile's text under the user's own session;
        it has no embedding provider. This backfills them. A profile without
        an embedding matches nothing (see `match_tenders_for_profile`), so
        this runs on the same schedule as ingestion.
        """
        profiles = self.repository.profiles_missing_embeddings(limit)
        if not profiles:
            return 0

        vectors = await self.embedder.embed(
            [p["description"] for p in profiles], input_type="query"
        )
        for profile, vector in zip(profiles, vectors):
            self.repository.set_profile_embedding(profile["id"], vector)

        logger.info("embedded %d alert profiles", len(profiles))
        return len(profiles)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _iso_date(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    return value.isoformat()
