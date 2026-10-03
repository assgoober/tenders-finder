# -*- coding: utf-8 -*-
"""A sweep that failed must say so to someone who is not reading logs.

HANDOFF work-queue item 2, written 22/09: "A failed source makes the sweep
exit 1 and send a digest, and no alarm reads either. `added_today = 0` is the
other signal nobody watches." `mod-balam` then harvested nothing for five
nights and the only evidence was an exit code in Task Scheduler's history.

Three holes, each covered here:

1. A source that raised was `continue`d past before its report was kept, so
   the operator digest -- built from the kept reports -- did not mention it.
   The source that failed was the one source the digest left out.
2. "0 new tenders" read as a clean run. At ~960 new tenders a day, zero is
   not a quiet night; it is a broken one.
3. Exit 2 (could not start) sent nothing at all, and a machine that is off
   sends nothing either. The first is fixed with a last-ditch email, the
   second can only be caught from outside the machine: a dead-man's-switch
   ping (`HEARTBEAT_URL`) that complains when it stops hearing from us.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from app.notifications import build_digest, needs_attention
from app.pipeline import IngestReport


# --- the digest -------------------------------------------------------------


def test_an_aborted_source_heads_the_digest():
    reports = [
        IngestReport(source_key="mod-balam", aborted=True, partial=True,
                     errors=["aborted: Executable doesn't exist"]),
        IngestReport(source_key="ted-eu", found=900, ingested=900),
    ]
    subject, body = build_digest(reports, new_tenders=900)
    assert "FAILED" in subject and "mod-balam" in subject
    assert "Executable doesn't exist" in body
    assert needs_attention(reports)


def test_zero_new_tenders_is_not_a_clean_run():
    reports = [IngestReport(source_key="ted-eu", found=40, ingested=40)]
    assert not needs_attention(reports)
    assert needs_attention(reports, new_tenders=0)
    subject, body = build_digest(reports, new_tenders=0)
    assert "NO NEW TENDERS" in subject
    assert "clean" not in subject


def test_a_partial_run_is_attention_even_without_errors():
    """`partial` was in the headline but not in `needs_attention`, so a
    partial run with no error strings was never sent."""
    assert needs_attention([IngestReport(source_key="uk-fts", found=10, partial=True)])


def test_unknown_new_count_changes_nothing():
    reports = [IngestReport(source_key="ted-eu", found=4, ingested=4)]
    assert build_digest(reports) == build_digest(reports, new_tenders=None)


@pytest.mark.asyncio
async def test_the_notifier_sends_on_zero_new(monkeypatch):
    from app.config import Settings
    from app.notifications import EmailNotifier

    notifier = EmailNotifier(Settings(smtp_host="smtp.example", notify_email="ops@example"))
    sent = []
    monkeypatch.setattr(notifier, "_send", lambda message: sent.append(message))

    clean = [IngestReport(source_key="ted-eu", found=3, ingested=3)]
    assert await notifier.send_run_digest(clean) is False
    assert await notifier.send_run_digest(clean, new_tenders=0) is True
    assert "NO NEW TENDERS" in sent[0]["Subject"]


# --- the scheduled script ---------------------------------------------------


def _patch(monkeypatch, run_source):
    import app.agents
    import app.pipeline as pipeline_module

    finished = []

    class _FakePipeline:
        def __init__(self, settings):
            self.settings = settings

        async def extract_matched(self):
            return 0

        async def run_source(self, agent, limit=25):
            return await run_source(agent)

        async def finish_sweep(self, reports, **kwargs):
            finished.append((list(reports), kwargs))
            return True

    monkeypatch.setattr(pipeline_module, "IngestionPipeline", _FakePipeline)
    monkeypatch.setattr(
        app.agents, "get_agent",
        lambda key, settings: SimpleNamespace(key=key, display_name=key),
    )
    return finished


@pytest.mark.asyncio
async def test_a_source_that_raised_is_in_the_digest(monkeypatch):
    import run_sweep

    async def run_source(agent):
        if agent.key == "mod-balam":
            raise RuntimeError("BrowserType.launch: Executable doesn't exist")
        return IngestReport(source_key=agent.key, found=2, ingested=2)

    finished = _patch(monkeypatch, run_source)
    args = SimpleNamespace(source=["mod-balam", "ted-eu"], limit=5, dry_run=True)
    code = await run_sweep.run(args, logging.getLogger("test-sweep"))

    reports, _ = finished[0]
    keys = [r.source_key for r in reports]
    assert keys == ["mod-balam", "ted-eu"], "the source that failed must not be dropped"
    assert reports[0].aborted and "Executable" in reports[0].errors[0]
    assert code == 1


@pytest.mark.asyncio
async def test_zero_new_tenders_costs_the_exit_code(monkeypatch):
    import run_sweep

    async def run_source(agent):
        return IngestReport(source_key=agent.key, found=2, ingested=2)

    finished = _patch(monkeypatch, run_source)
    monkeypatch.setattr(run_sweep, "count_tenders", lambda settings, since: (5645, 0))
    args = SimpleNamespace(source=["ted-eu"], limit=5, dry_run=False)
    code = await run_sweep.run(args, logging.getLogger("test-sweep"))

    assert finished[0][1] == {"new_tenders": 0}
    assert code == 1


@pytest.mark.asyncio
async def test_new_tenders_keep_a_clean_exit(monkeypatch):
    import run_sweep

    async def run_source(agent):
        return IngestReport(source_key=agent.key, found=2, ingested=2)

    finished = _patch(monkeypatch, run_source)
    monkeypatch.setattr(run_sweep, "count_tenders", lambda settings, since: (5647, 2))
    args = SimpleNamespace(source=["ted-eu"], limit=5, dry_run=False)
    assert await run_sweep.run(args, logging.getLogger("test-sweep")) == 0
    assert finished[0][1] == {"new_tenders": 2}


# --- the heartbeat ----------------------------------------------------------


def test_heartbeat_is_silent_without_a_url(monkeypatch):
    import run_sweep

    calls = []
    monkeypatch.delenv("HEARTBEAT_URL", raising=False)
    monkeypatch.setattr(run_sweep, "_post", lambda url, body: calls.append(url))
    run_sweep.heartbeat("start")
    assert calls == []


def test_heartbeat_reports_the_exit_code(monkeypatch):
    import run_sweep

    calls = []
    monkeypatch.setenv("HEARTBEAT_URL", "https://hc-ping.com/abc/")
    monkeypatch.setattr(run_sweep, "_post", lambda url, body: calls.append((url, body)))
    run_sweep.heartbeat("start")
    run_sweep.heartbeat(1, "tail of the log")
    assert calls == [
        ("https://hc-ping.com/abc/start", ""),
        ("https://hc-ping.com/abc/1", "tail of the log"),
    ]


def test_heartbeat_never_raises(monkeypatch):
    import run_sweep

    def boom(url, body):
        raise OSError("network down")

    monkeypatch.setenv("HEARTBEAT_URL", "https://hc-ping.com/abc")
    monkeypatch.setattr(run_sweep, "_post", boom)
    run_sweep.heartbeat(0)  # must not raise: the ping is a report, not the job


def test_could_not_start_sends_an_alarm(monkeypatch, tmp_path):
    """Exit 2 used to end the run without a word to anyone."""
    import run_sweep

    alarms, pings = [], []

    async def explode(args, log):
        raise RuntimeError("SUPABASE_URL is not set")

    monkeypatch.setattr(run_sweep, "run", explode)
    monkeypatch.setattr(run_sweep, "send_alarm", lambda s, b: alarms.append((s, b)))
    monkeypatch.setattr(run_sweep, "heartbeat", lambda code, body="": pings.append(code))
    monkeypatch.setattr("sys.argv", ["run_sweep.py", "--log-dir", str(tmp_path)])

    assert run_sweep.main() == 2
    assert alarms and "could not start" in alarms[0][0].lower()
    assert "SUPABASE_URL" in alarms[0][1]
    assert pings == ["start", 2]


# --- a missing browser names the account and the path ----------------------


def test_a_missing_browser_says_where_it_looked(monkeypatch):
    import run_sweep

    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", r"C:\x\.playwright-browsers")
    exc = RuntimeError(
        r"BrowserType.launch: Executable doesn't exist at C:\...\headless_shell.exe"
    )
    hint = run_sweep.browser_hint(exc)
    assert hint and "run_sweep.cmd" in hint and ".playwright-browsers" in hint
    assert run_sweep.browser_hint(RuntimeError("timeout")) is None


@pytest.mark.asyncio
async def test_the_digest_carries_the_browser_hint(monkeypatch):
    import run_sweep

    async def run_source(agent):
        raise RuntimeError("BrowserType.launch: Executable doesn't exist at X")

    finished = _patch(monkeypatch, run_source)
    args = SimpleNamespace(source=["mod-balam"], limit=5, dry_run=True)
    await run_sweep.run(args, logging.getLogger("test-sweep"))

    errors = finished[0][0][0].errors
    assert any(e.startswith("hint:") for e in errors)
