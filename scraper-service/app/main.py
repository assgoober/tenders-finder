"""FastAPI surface for the aggregation and extraction service.

Kept small on purpose: this is an internal control plane for a background
pipeline, not a public API. The dashboard reads from Supabase directly under
RLS; it never calls this service.
"""

from __future__ import annotations

import logging

from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, HTTPException
from pydantic import BaseModel, Field

from .agents import available_keys, describe_agents
from .config import EMBEDDING_DIM, get_settings
from .nlp.extractor import TenderExtractor
from .nlp.prompts import PROMPT_VERSION
from .pipeline import IngestionPipeline

logging.basicConfig(level=get_settings().log_level)
logger = logging.getLogger(__name__)

def validate_configuration() -> None:
    """Fail loudly at startup rather than deep inside a run.

    The dimension check in particular: a provider/schema mismatch otherwise
    surfaces as an opaque INSERT error after the expensive work is done.
    """
    settings = get_settings()

    if settings.embedding_dim != EMBEDDING_DIM:
        raise RuntimeError(
            f"EMBEDDING_DIM is {settings.embedding_dim} but the schema declares "
            f"vector({EMBEDDING_DIM}). Changing this requires a migration and a "
            "full re-embedding (REPOMIX.md ADR-6)."
        )

    if not settings.has_anthropic:
        logger.warning("ANTHROPIC_API_KEY is not set — extraction endpoints will fail")
    if not settings.has_supabase and not settings.dry_run:
        logger.warning("Supabase is not configured — set DRY_RUN=true to run without a database")

    logger.info("registered agents: %s", ", ".join(available_keys()) or "(none)")


@asynccontextmanager
async def lifespan(_: FastAPI):
    validate_configuration()
    yield


app = FastAPI(
    title="Tenders Finder — Aggregation Service",
    version="0.1.0",
    description=(
        "Scrapes tender portals, extracts structured data with Claude, "
        "and stores vectors for semantic matching."
    ),
    lifespan=lifespan,
)


class IngestRequest(BaseModel):
    sources: list[str] | None = Field(
        default=None, description="Agent keys to run. Omit to run every registered agent."
    )
    limit: int = Field(default=25, ge=1, le=500, description="Max tenders per source.")


class ExtractRequest(BaseModel):
    raw_text: str = Field(min_length=1)
    source_url: str | None = None
    source_name: str | None = None


@app.get("/health")
def health() -> dict:
    settings = get_settings()
    return {
        "status": "ok",
        "model": settings.anthropic_model,
        "prompt_version": PROMPT_VERSION,
        "embedding_provider": settings.embedding_provider,
        "embedding_dim": settings.embedding_dim,
        "anthropic_configured": settings.has_anthropic,
        "supabase_configured": settings.has_supabase,
        "dry_run": settings.dry_run,
        "agents": available_keys(),
    }


@app.get("/sources")
def sources() -> list[dict]:
    return describe_agents()


@app.post("/ingest")
async def ingest(request: IngestRequest) -> dict:
    """Run the pipeline synchronously and return per-source results."""
    unknown = set(request.sources or []) - set(available_keys())
    if unknown:
        raise HTTPException(status_code=404, detail=f"unknown sources: {sorted(unknown)}")

    pipeline = IngestionPipeline()
    reports = await pipeline.run(request.sources, limit=request.limit)
    return {"reports": [r.as_dict() for r in reports]}


@app.post("/ingest/async", status_code=202)
async def ingest_async(request: IngestRequest, background: BackgroundTasks) -> dict:
    """Kick off a run and return immediately.

    A full portal sweep runs for minutes; this is what a scheduler calls.
    Progress is readable from `scrape_runs`.
    """
    unknown = set(request.sources or []) - set(available_keys())
    if unknown:
        raise HTTPException(status_code=404, detail=f"unknown sources: {sorted(unknown)}")

    async def task() -> None:
        pipeline = IngestionPipeline()
        reports = await pipeline.run(request.sources, limit=request.limit)
        for report in reports:
            logger.info("ingest complete: %s", report.as_dict())

    background.add_task(task)
    return {"status": "accepted", "sources": request.sources or available_keys()}


@app.post("/extract")
async def extract(request: ExtractRequest) -> dict:
    """Extract one document without persisting it.

    Used for prompt iteration and for the human-review screen.
    """
    settings = get_settings()
    if not settings.has_anthropic:
        raise HTTPException(status_code=503, detail="ANTHROPIC_API_KEY is not configured")

    result = await TenderExtractor(settings).extract(
        request.raw_text,
        source_name=request.source_name,
        source_url=request.source_url,
    )

    return {
        "status": result.status,
        "attempts": result.attempts,
        "model": result.model,
        "prompt_version": result.prompt_version,
        "error": result.error,
        "truncated_input": result.truncated_input,
        "extraction": result.extraction.model_dump(mode="json") if result.extraction else None,
    }


@app.post("/profiles/embed")
async def embed_profiles(limit: int = 100) -> dict:
    """Backfill embeddings for alert profiles created in the dashboard."""
    pipeline = IngestionPipeline()
    count = await pipeline.embed_pending_profiles(limit)
    return {"embedded": count}
