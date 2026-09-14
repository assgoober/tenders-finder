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
from datetime import datetime, timezone

from .agents import FetchedTender, TenderSourceAgent, TenderStub, get_agents
from .config import Settings, get_settings
from .nlp.embeddings import EmbeddingProvider, get_embedding_provider
from .nlp.extractor import (
    ExtractionNotConfigured,
    ExtractionQuotaExhausted,
    ExtractionResult,
    TenderExtractor,
    get_extractor,
)
from .nlp.prompts import PROMPT_VERSION
from .notifications import (
    Notifier,
    build_match_alert,
    get_notifier,
    group_alerts_by_recipient,
)
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
    #: Tenders already extracted, unchanged since, under the same prompt and
    #: model -- so re-fetching and re-extracting them would spend a browser
    #: launch and an LLM call to arrive at the row that is already there.
    skipped: int = 0
    #: True when this run covers less than the source offered, for ANY
    #: reason: a listing page that would not turn, a continuation token the
    #: request never asked for, or a provider quota that ran out mid-run.
    #:
    #: The field started out meaning only the first of those, and on
    #: 2026-09-11 a TED sweep that ingested 483 of 551 and aborted on quota
    #: reported `partial: false` with `failed: 0`. Both were literally true
    #: and the pair read as a complete run -- which is the third time in this
    #: codebase that work not done failed to reach whoever read the result.
    partial: bool = False
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "source": self.source_key,
            "found": self.found,
            "ingested": self.ingested,
            "needs_review": self.needs_review,
            "refused": self.refused,
            "failed": self.failed,
            "skipped": self.skipped,
            "partial": self.partial,
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

        # Before the digest, so a mail failure cannot skip it, and never
        # inside a source's try: the clock is a property of the corpus, not of
        # any one portal. Failure here is logged, not raised -- a sweep that
        # harvested correctly is not a failed sweep because a status refresh
        # could not run.
        # Unlike extraction, this one IS conditioned on dry_run: it is a write
        # to shared rows, and a dry run that mutates the corpus is not a dry
        # run.
        if not self.settings.dry_run:
            try:
                aged = await asyncio.to_thread(self.repository.age_tender_status)
                if any(aged.values()):
                    logger.info("aged tender status: %s", aged)
            except Exception as exc:  # noqa: BLE001
                logger.warning("could not age tender status: %s", exc)

        # Customer alerts before the operator digest. If only one of the two
        # can get out, it should be the one a bid team is waiting for.
        if not self.settings.dry_run:
            try:
                delivered = await self.deliver_match_alerts()
                if delivered:
                    logger.info("delivered %d match alerts", delivered)
            except Exception as exc:  # noqa: BLE001
                logger.error("could not deliver match alerts: %s", exc)

        # After every source has finished, so one mail covers the whole run.
        # Never inside the try of a source: a mail failure must not be able to
        # mark a successful scrape as failed.
        await self.notifier.send_run_digest(reports)

        return reports

    async def deliver_match_alerts(self, limit: int = 500) -> int:
        """Send every undelivered match to the people it belongs to.

        Returns the number of matches stamped as delivered -- which is not the
        number of emails, because one message carries every match for one
        recipient. A customer with seven matches gets one email, not seven;
        seven is how an alert becomes a filter rule.
        """
        rows = await asyncio.to_thread(self.repository.pending_match_alerts, limit)
        if not rows:
            return 0

        delivered = 0
        for (recipient, organization), matches in group_alerts_by_recipient(rows).items():
            subject, body = build_match_alert(organization, matches)
            if not await self.notifier.send_match_alert(recipient, subject, body):
                # Left pending on purpose. A match marked delivered after a
                # failed send is a match nobody will ever be told about.
                continue
            ids = [m["match_id"] for m in matches]
            delivered += await asyncio.to_thread(self.repository.mark_matches_alerted, ids)

        return delivered

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
            # A truncated walk is not an error -- the rows it did get are good
            # -- but it must not be reported as a complete sweep. Without this
            # the digest, the run row and the log all say the same thing for
            # 60 of 185 tenders as they do for all 185.
            report.partial = bool(getattr(agent, "pagination_truncated", False))
            if report.partial:
                report.errors.append(
                    "listing pagination stopped early; this harvest is partial"
                )
                logger.warning(
                    "[%s] PARTIAL harvest: %d tenders before pagination stopped",
                    agent.key, report.found,
                )
            else:
                logger.info("[%s] discovered %d tenders", agent.key, report.found)

            # One query for the source, before the loop. Fetching a detail
            # page launches a browser per tender, so the skip has to happen
            # before the fetch to be worth anything.
            fingerprints: dict[str, dict] = {}
            if not self.settings.dry_run:
                try:
                    fingerprints = self.repository.extracted_fingerprints(source_id)
                except Exception as exc:  # noqa: BLE001
                    # Not being able to read the index is a reason to do the
                    # work, never a reason to fail the run.
                    logger.warning("[%s] could not load extraction index: %s", agent.key, exc)

            for stub in stubs:
                try:
                    if self._already_current(stub, fingerprints.get(stub.external_id)):
                        report.skipped += 1
                        continue
                    if self.settings.extract_on_ingest:
                        fetched = await agent.fetch(stub)
                        await self._ingest_one(fetched, source_id, report)
                    else:
                        # No detail fetch, no browser, no model call. The
                        # listing is already in hand from discover().
                        await self._ingest_listing(stub, source_id, report)
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
                    # A run that stopped part-way through its own stub list is
                    # a partial harvest. Leaving this to `errors` alone means
                    # a reader sees partial=false, failed=0 and concludes the
                    # sweep finished.
                    report.partial = True
                    break
                except Exception as exc:  # noqa: BLE001
                    # One bad tender must not abort the run — the remaining
                    # tenders in this portal are still worth ingesting.
                    report.failed += 1
                    report.errors.append(f"{stub.external_id}: {type(exc).__name__}: {exc}")
                    logger.exception("[%s] ingest failed for %s", agent.key, stub.external_id)

            if report.skipped:
                logger.info(
                    "[%s] skipped %d of %d already-current tenders",
                    agent.key, report.skipped, report.found,
                )

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

    def _already_current(self, stub, fingerprint: dict | None) -> bool:
        """Whether this tender is already stored, extracted, and unchanged.

        Four things must all agree, and each of them is a way the stored row
        could be out of date:

        * the tender was extracted successfully at all;
        * the listing row is byte-identical, so the portal has not edited it
          (the row carries the portal's own last-updated date, which is what
          makes this a change detector rather than a guess);
        * the prompt version matches, so a prompt change re-extracts the
          corpus instead of leaving a mixture of two prompt generations;
        * the model matches, for the same reason.

        Anything unknown on either side counts as a mismatch and re-extracts.
        A skip that is wrong is invisible -- it looks exactly like a tender
        nobody changed -- so this errs toward doing the work.
        """
        if not fingerprint:
            return False

        stored_row = fingerprint.get("raw_row")
        current_row = (stub.listing_metadata or {}).get("raw_row")
        if not stored_row or not current_row or stored_row != current_row:
            return False

        if fingerprint.get("prompt_version") != PROMPT_VERSION:
            return False

        return fingerprint.get("model") == self.settings.extraction_model

    async def _ingest_listing(
        self, stub, source_id: str | None, report: IngestReport
    ) -> None:
        """Store and embed a tender from its listing, without reading it.

        One embedding call. No document fetch, no browser, no extraction.
        This is what makes 11,664 notices reachable where 551 was not.

        `extraction_status` stays `pending` -- which is true, and which the
        column already meant. The row says where its vector came from under
        `metadata.embedding.source`, so nothing downstream has to infer it
        and a half-read corpus can always be counted.
        """
        text = stub.listing_text()
        if not text:
            # A listing with no words at all cannot be embedded, and an
            # unembedded row is invisible to matching. Better to count it as
            # failed here than to store something no query will ever return.
            report.failed += 1
            report.errors.append(f"{stub.external_id}: listing carried no text to embed")
            return

        vectors = await self.embedder.embed([text], input_type="document")

        metadata = dict(stub.listing_metadata or {})
        row = {
            "source_id": source_id,
            "external_id": stub.external_id,
            "source_url": stub.source_url,
            "title": stub.title,
            "buyer_name": stub.buyer_name,
            "publication_date": stub.publication_date.date().isoformat()
            if stub.publication_date else None,
            "deadline": stub.deadline.isoformat() if stub.deadline else None,
            "status": "open",
            "metadata": {
                "listing": metadata,
                "embedding": {
                    "source": "listing",
                    "model": self.settings.gemini_embedding_model,
                    "chars": len(text),
                    "at": datetime.now(timezone.utc).isoformat(),
                },
            },
            "embedding": vectors[0],
            "extraction_status": "pending",
        }

        report.ingested += 1
        if self.settings.dry_run:
            return
        self.repository.upsert_tender(row)

    async def extract_matched(self, limit: int | None = None) -> int:
        """Read the tenders somebody actually matched.

        This is the other half of the inversion. Ingestion got cheap by not
        reading anything; this is where reading happens, and it is spent only
        on tenders a real capability profile scored above the floor -- so the
        model call is made after the evidence that someone wants it, not
        before.

        Returns the number extracted. Failures are logged and left in the
        queue: the migration guarantees a failed extraction cannot take the
        listing embedding with it, so a tender that could not be read stays
        matchable on what the buyer published.
        """
        limit = limit or self.settings.extract_matched_limit
        try:
            queue = await asyncio.to_thread(self.repository.tenders_awaiting_extraction, limit)
        except Exception as exc:  # noqa: BLE001
            logger.error("could not read the extraction queue: %s", exc)
            return 0

        if not queue:
            return 0

        logger.info("extracting %d matched tenders", len(queue))
        done = 0
        for item in queue:
            try:
                agent = self._agent_for_source(item)
                if agent is None:
                    continue
                stub = TenderStub(
                    external_id=item["external_id"],
                    title=item.get("title") or "",
                    source_url=item["source_url"],
                )
                fetched = await agent.fetch(stub)
                report = IngestReport(source_key=agent.key)
                await self._ingest_one(fetched, None, report)
                done += report.ingested
            except ExtractionQuotaExhausted as exc:
                # Same reasoning as the ingest loop: a spent quota is a
                # property of the run. Stop and keep the queue for tomorrow.
                logger.error("extraction budget exhausted after %d tenders: %s", done, exc)
                break
            except Exception as exc:  # noqa: BLE001
                logger.warning("could not extract %s: %s", item.get("external_id"), exc)

        return done

    def _agent_for_source(self, item: dict):
        """Which agent can fetch this tender's document.

        Matched on the source URL rather than stored on the queue row: the
        queue is a database function and should not need to know the name of
        a Python class.
        """
        url = item.get("source_url") or ""
        for agent in get_agents(None, self.settings):
            base = getattr(agent, "base_url", "") or ""
            host = base.split("//")[-1].split("/")[0]
            if host and host in url:
                return agent
        logger.warning("no agent recognises %s", url[:80])
        return None

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
