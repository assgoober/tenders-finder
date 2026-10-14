# -*- coding: utf-8 -*-
"""The scheduled sweep must deliver the alerts it caused to exist.

Harvesting is not the product. A tender lands, a database trigger matches it
against a customer's profile, and the customer is told -- that last step is
the thing anyone would pay for, and until 2026-09-18 the scheduled sweep did
not take it.

Not because it was broken. `deliver_match_alerts()` was written, correct and
covered. It simply lived at the tail of `IngestionPipeline.run()`, and
`run_sweep.py` -- the script Task Scheduler actually runs -- drives
`run_source()` in a loop of its own so it can order the sources by priority
and report each separately. Two orchestrators; only one of them finished the
job. The morning run harvested, wrote, matched, and told nobody.

The visible state on the day it was found: 31 matches waiting, and 79 already
delivered -- every one of those 79 because a human asked for them by hand.

So the tests here are not about whether alerts can be sent. That was never in
question, and a test asserting it would have passed throughout. They are
about whether the path that runs every morning reaches them.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.pipeline import IngestReport, IngestionPipeline


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


class _Notifier:
    def __init__(self):
        self.digests = 0

    async def send_run_digest(self, reports):
        self.digests += 1


def _pipeline(settings, *, delivered=3, raises=None):
    pipeline = IngestionPipeline(settings=settings)
    pipeline.notifier = _Notifier()

    calls = {"deliver": 0}

    async def _deliver(limit=500):
        calls["deliver"] += 1
        if raises is not None:
            raise raises
        return delivered

    pipeline.deliver_match_alerts = _deliver
    pipeline.repository = SimpleNamespace(
        age_tender_status=lambda: {},
        # finish_sweep also embeds dashboard profiles now; none are pending here.
        profiles_missing_embeddings=lambda limit=100: [],
    )
    return pipeline, calls


# --- the finishing work, on its own ---------------------------------------

@pytest.mark.asyncio
async def test_finishing_a_sweep_delivers_the_alerts():
    pipeline, calls = _pipeline(make_settings())

    assert await pipeline.finish_sweep([]) is True
    assert calls["deliver"] == 1
    assert pipeline.notifier.digests == 1


@pytest.mark.asyncio
async def test_a_failed_delivery_is_reported_rather_than_swallowed():
    """Mail being down must not raise -- a harvest that worked is still a
    harvest -- but it must not read as a clean run either. That distinction
    is the only reason `finish_sweep` returns anything at all."""
    pipeline, calls = _pipeline(make_settings(), raises=RuntimeError("smtp is down"))

    assert await pipeline.finish_sweep([]) is False
    assert calls["deliver"] == 1
    # The operator digest still goes out: it is the message that says what
    # happened, and it is most needed on the runs that went badly.
    assert pipeline.notifier.digests == 1


@pytest.mark.asyncio
async def test_a_dry_run_sends_nothing_to_customers():
    """A dry run that emails a bid team is not a dry run."""
    pipeline, calls = _pipeline(make_settings(dry_run=True))

    assert await pipeline.finish_sweep([]) is True
    assert calls["deliver"] == 0


# --- the path that actually runs every morning ----------------------------

@pytest.mark.asyncio
async def test_the_scheduled_script_finishes_the_sweep(monkeypatch):
    """The regression itself.

    `run_sweep.run()` is the function Task Scheduler reaches. Before the fix
    it ran its source loop and returned, and this test failed -- not on an
    assertion about alerts, but because the call was not there at all.
    """
    import app.agents
    import app.pipeline as pipeline_module
    import run_sweep

    finished = []

    class _FakePipeline:
        def __init__(self, settings):
            self.settings = settings

        async def run_source(self, agent, limit=25):
            return IngestReport(source_key=agent.key, found=2, ingested=2)

        async def finish_sweep(self, reports):
            finished.append(list(reports))
            return True

    monkeypatch.setattr(pipeline_module, "IngestionPipeline", _FakePipeline)
    monkeypatch.setattr(
        app.agents, "get_agent",
        lambda key, settings: SimpleNamespace(key=key, display_name=key),
    )

    args = SimpleNamespace(source=["ted-eu", "mod-balam"], limit=5, dry_run=True)
    code = await run_sweep.run(args, logging.getLogger("test-sweep"))

    assert finished, "the scheduled sweep returned without finishing the sweep"
    assert len(finished[0]) == 2, (
        "the digest must cover every source in the run, not the last one"
    )
    assert code == 0


@pytest.mark.asyncio
async def test_undelivered_alerts_cost_the_sweep_its_exit_code(monkeypatch):
    """Exit 0 means the run finished. A sweep whose alerts never left the
    building did not finish, and the scheduler is the only thing watching."""
    import app.agents
    import app.pipeline as pipeline_module
    import run_sweep

    class _FakePipeline:
        def __init__(self, settings):
            pass

        async def run_source(self, agent, limit=25):
            return IngestReport(source_key=agent.key, found=1, ingested=1)

        async def finish_sweep(self, reports):
            return False

    monkeypatch.setattr(pipeline_module, "IngestionPipeline", _FakePipeline)
    monkeypatch.setattr(
        app.agents, "get_agent",
        lambda key, settings: SimpleNamespace(key=key, display_name=key),
    )

    args = SimpleNamespace(source=["ted-eu"], limit=5, dry_run=True)
    code = await run_sweep.run(args, logging.getLogger("test-sweep"))

    assert code == 1
