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
from .nlp.extractor import (
    ExtractionNotConfigured,
    ExtractionQuotaExhausted,
    ExtractionResult,
    TenderExtractor,
    get_extractor,
)
from .notifications import Notifier, get_notifier
from .storage.supabase_repo import SupabaseRepository

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class IngestReport:
    source_key: str
    found: int = 0
    ingested: int = 0
    needs_review: int = 0
    #: Subset of `needs_review` that a safety classifier declined rather than
    #: failed schema validation. Counted separately because the two call for
    #: different responses: a schema failure is a prompt or a document
    #: problem, a wall of refusals is a policy boundary.
    refused: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "source": self.source_key,
            "found": self.found,
            "ingested": self.ingested,
            "needs_review": self.needs_review,
            "refused": self.refused,
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
        notifier: Notifier | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.extractor = extractor or get_extractor(self.settings)
        self.embedder = embedder or get_embedding_provider(self.settings)
        self.repository = repository or SupabaseRepository(self.settings)
        self.notifier = notifier or get_notifier(self.settings)

    async def run(
        self, source_keys: list[str] | None = None, *, limit: int = 25
    ) -> list[IngestReport]:
        # Checked before a single request leaves the machine. Extraction runs
        # per tender, so without this a sweep of a nine-page portal contacts
        # it ~190 times, extracts every row, and then fails every one of them
        # for a reason that was knowable before the first request. Refusing up
        # front costs the portal nothing and reports the real cause once.
        #
        # Deliberately not conditioned on `dry_run`: a dry run still extracts,
        # and pretending otherwise is how "the pipeline ran clean" comes to
        # mean "the pipeline did not run".
        if not self.settings.has_extraction:
            provider = self.settings.extraction_provider
            key = "GEMINI_API_KEY" if provider == "gemini" else "ANTHROPIC_API_KEY"
            raise ExtractionNotConfigured(
                f"EXTRACTION_PROVIDER is {provider!r} but {key} is not set, so this run "
                "would scrape every source and then fail to extract every tender. Set it "
                "in the service's .env before running the pipeline."
            )

        agents = get_agents(source_keys, self.settings)
        # Bounded concurrency: several portals at once, but never enough
        # simultaneous traffic at one of them to look like an attack.
        semaphore = asyncio.Semaphore(self.settings.scrape_concurrency)

        async def guarded(agent: TenderSourceAgent) -> IngestReport:
            async with semaphore:
                return await self.run_source(agent, limit=limit)

        reports = list(await asyncio.gather(*(guarded(agent) for agent in agents)))

        # After every source has finished, so one mail covers the whole run.
        # Never inside the try of a source: a mail failure must not be able to
        # mark a successful scrape as failed.
        await self.notifier.send_run_digest(reports)

        return reports

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
            quota_exhausted = False
            stubs = await agent.discover(limit=limit)
            report.found = len(stubs)
            logger.info("[%s] discovered %d tenders", agent.key, report.found)

            for stub in stubs:
                try:
                    fetched = await agent.fetch(stub)
                    await self._ingest_one(fetched, source_id, report)
                except ExtractionQuotaExhausted as exc:
                    # Deliberately breaks the loop that the clause below
                    # deliberately does not. A bad document is local to one
                    # tender; an exhausted quota is a property of the run, so
                    # continuing would fail every remaining row for the same
                    # reason and overwrite whatever each already holds. Stop,
                    # say how far we got, and mark the run not-ok so the digest
                    # and scrape_runs both show a partial sweep rather than a
                    # complete one.
                    report.errors.append(f"run aborted: {exc}")
                    logger.error(
                        "[%s] %s -- stopping after %d of %d tenders",
                        agent.key, exc, report.ingested + report.needs_review + report.failed,
                        report.found,
                    )
                    quota_exhausted = True
                    break
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
                    ok=not quota_exhausted,
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
            # A declined document is unvalidated for a different reason but
            # lands in the same place — nobody has read it.
            report.needs_review += 1
            if result.refused:
                report.refused += 1
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
            # Both null on the ordinary path. Present so the review queue can
            # tell a reviewer why a tender is in front of them, and so a
            # fallback rescue is visible rather than invisible.
            "refusal_category": result.refusal_category,
            "served_by": result.served_by,
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
