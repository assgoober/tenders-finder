# -*- coding: utf-8 -*-
"""A profile written in the dashboard must reach the matcher on its own.

HANDOFF work-queue item 5: "Onboarding is a SQL insert." The dashboard can
now write alert profiles, but it has no embedding provider, so it stores
the text with a null vector. `embed_pending_profiles()` existed to fill that
in -- and was reachable only from `POST /profiles/embed`, which nothing
schedules. The same shape as the alert-delivery bug of 18/09: correct code
that the path running every morning never reaches.

Two things are tested: the sweep's finishing step embeds pending profiles,
and each one is backfilled against the tenders already held, because the
tender-side trigger never looks backwards.
"""
from __future__ import annotations

import pytest

from app.config import Settings
from app.pipeline import IngestionPipeline


def make_settings(**kw) -> Settings:
    base = dict(
        extraction_provider="gemini",
        gemini_api_key="k",
        embedding_provider="hash",
        dry_run=False,
        extract_on_ingest=False,
    )
    base.update(kw)
    return Settings(**base)


class _Repo:
    def __init__(self, pending, backfill_raises=False):
        self.pending = pending
        self.embedded = {}
        self.backfilled = []
        self.backfill_raises = backfill_raises

    def profiles_missing_embeddings(self, limit=100):
        return [p for p in self.pending if p["id"] not in self.embedded]

    def set_profile_embedding(self, profile_id, embedding):
        self.embedded[profile_id] = embedding

    def generate_matches_for_profile(self, profile_id):
        if self.backfill_raises:
            raise RuntimeError("rpc failed")
        self.backfilled.append(profile_id)
        return 7

    # Everything else finish_sweep touches.
    def age_tender_status(self):
        return {}

    def pending_match_alerts(self, limit=500):
        return []


class _Notifier:
    async def send_run_digest(self, reports, **kwargs):
        return False


def _pipeline(repo, **settings):
    pipeline = IngestionPipeline(settings=make_settings(**settings), repository=repo)
    pipeline.notifier = _Notifier()
    return pipeline


@pytest.mark.asyncio
async def test_embedding_a_profile_backfills_its_matches():
    repo = _Repo([{"id": "p1", "description": "Tactical radio systems for armed forces"}])
    pipeline = _pipeline(repo)

    assert await pipeline.embed_pending_profiles() == 1
    assert "p1" in repo.embedded
    assert repo.backfilled == ["p1"], "a new profile must be scored against existing tenders"


@pytest.mark.asyncio
async def test_the_sweep_embeds_profiles_written_in_the_dashboard():
    repo = _Repo([{"id": "p1", "description": "Night vision optics and thermal imaging"}])
    pipeline = _pipeline(repo)

    assert await pipeline.finish_sweep([]) is True
    assert "p1" in repo.embedded, "the morning run never embedded dashboard profiles"
    assert repo.backfilled == ["p1"]


@pytest.mark.asyncio
async def test_a_failed_backfill_keeps_the_embedding():
    """The vector is still right; tomorrow's tenders reach the profile through
    the trigger. Losing the embedding over a backfill error would cost both."""
    repo = _Repo([{"id": "p1", "description": "Armoured vehicle maintenance"}],
                 backfill_raises=True)
    pipeline = _pipeline(repo)

    assert await pipeline.embed_pending_profiles() == 1
    assert "p1" in repo.embedded


@pytest.mark.asyncio
async def test_a_dry_run_embeds_nothing():
    repo = _Repo([{"id": "p1", "description": "Satellite ground stations"}])
    pipeline = _pipeline(repo, dry_run=True)

    await pipeline.finish_sweep([])
    assert repo.embedded == {}
