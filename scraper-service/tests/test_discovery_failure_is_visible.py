# -*- coding: utf-8 -*-
"""A source that could not be looked at must not report as a source with
nothing new.

The two outcomes produce almost the same numbers -- nothing found, nothing
ingested, nothing failed -- and they mean opposite things. One is a quiet
night at the buyer's end. The other is a broken sweep, and on the Israeli MoD
source it means the home market is missing from the corpus.

On 2026-09-18 the distinction did not exist. The agent could not launch its
browser, discovery raised, and the report came back:

    found 0   ingested 0   skipped 0   failed 0   partial False

`run_sweep.py` decides its exit code with `if report.partial or report.failed`,
so the sweep exited 0 and Windows Task Scheduler recorded success. Nothing in
the chain was lying; the one field that knew was never set.

The test that would have caught it is not "does a failure set a flag" -- that
passes just as well in a codebase where every run is partial. It is "are these
two runs distinguishable", so that is what is written here.
"""
from __future__ import annotations

import pytest

from app.agents.base import FetchedTender, TenderStub
from app.config import Settings
from app.pipeline import IngestionPipeline


def make_settings(**kw) -> Settings:
    base = dict(
        extraction_provider="gemini",
        gemini_api_key="k",
        embedding_provider="hash",
        dry_run=True,
        extract_on_ingest=False,
    )
    base.update(kw)
    return Settings(**base)


class _Agent:
    """A source whose discovery either returns stubs or raises."""

    key = "fake"
    display_name = "fake"
    base_url = "https://example.invalid"
    country = "IL"
    sector = "defence"

    def __init__(self, *, raises=None, stubs=()):
        self._raises = raises
        self._stubs = list(stubs)

    async def discover(self, limit=25):
        if self._raises is not None:
            raise self._raises
        return self._stubs

    async def fetch(self, stub):
        return FetchedTender(stub=stub, raw_text="the whole document")


async def _run(monkeypatch, agent):
    monkeypatch.setattr("app.pipeline.get_agents", lambda *a, **k: [agent])
    pipeline = IngestionPipeline(settings=make_settings())
    (report,) = await pipeline.run(["fake"], limit=5)
    return report


# --- the defect -----------------------------------------------------------

@pytest.mark.asyncio
async def test_a_source_that_could_not_be_reached_reports_partial(monkeypatch):
    """The exact failure of 2026-09-18, in the exact shape it arrived.

    Playwright raises when its browser binary cannot be launched. Everything
    downstream of that is correct -- there is genuinely nothing to ingest --
    and that is precisely why it went unnoticed.
    """
    boom = RuntimeError(
        "BrowserType.launch: Executable doesn't exist at "
        r"C:\ms-playwright\chromium_headless_shell-1148\chrome-win\headless_shell.exe"
    )
    report = await _run(monkeypatch, _Agent(raises=boom))

    assert report.found == 0
    assert report.ingested == 0
    assert report.failed == 0          # nothing was attempted, so nothing failed
    assert report.partial is True      # ...but the run covered none of the source
    assert any(e.startswith("discover:") for e in report.errors)


@pytest.mark.asyncio
async def test_a_source_with_nothing_new_does_not_report_partial(monkeypatch):
    """The other side of the same coin, and the reason `partial` cannot simply
    be set whenever `found` is zero: a source that was checked and had nothing
    new is a complete run, and flagging it every quiet night would teach
    whoever reads the log to ignore the flag."""
    report = await _run(monkeypatch, _Agent(stubs=[]))

    assert report.found == 0
    assert report.ingested == 0
    assert report.failed == 0
    assert report.partial is False
    assert report.errors == []


@pytest.mark.asyncio
async def test_the_two_outcomes_are_distinguishable(monkeypatch):
    """The property that actually matters, stated directly rather than implied
    by the two tests above."""
    broken = await _run(monkeypatch, _Agent(raises=RuntimeError("no browser")))
    quiet = await _run(monkeypatch, _Agent(stubs=[]))

    assert (broken.found, broken.ingested, broken.failed) == (
        quiet.found, quiet.ingested, quiet.failed
    ), "the counters alone cannot tell these apart -- that is the premise"

    assert broken.partial != quiet.partial, (
        "a sweep that could not look and a sweep that found nothing must not "
        "report identically"
    )


# --- the contract the scheduler reads -------------------------------------

@pytest.mark.asyncio
async def test_the_exit_code_condition_now_sees_it(monkeypatch):
    """`run_sweep.py` computes its exit code with exactly this expression.

    It is reproduced rather than imported because importing the script pulls in
    argument parsing and a repository connection, and the expression is the
    whole contract. The documented meaning of exit 0 is "every source finished
    and wrote what it found", which a source that could not start its browser
    plainly did not.
    """
    broken = await _run(monkeypatch, _Agent(raises=RuntimeError("no browser")))
    quiet = await _run(monkeypatch, _Agent(stubs=[]))

    def worst(report) -> int:
        return 1 if (report.partial or report.failed) else 0

    assert worst(broken) == 1, "a failed sweep must not exit 0"
    assert worst(quiet) == 0, "a quiet night must not exit 1"
