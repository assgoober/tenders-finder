#!/usr/bin/env python
"""Run a full open-tender sweep. Standalone, unattended, no LLM in the loop.

Why this exists as a script rather than as a scheduled Claude session.

The first attempt wrapped this work in a scheduled agent session. It started,
ran for three minutes, and then stopped: `isRunning: true`, no log file, no
rows, no activity for thirteen minutes. Whatever it was waiting for -- a
permission prompt, a wedged turn -- it was waiting silently, and at 10:05 the
next morning nobody would have been awake to notice.

The work itself is deterministic: fetch, filter, embed, upsert. There is no
judgement in it. Wrapping a four-hour batch job in a conversation adds a
failure mode that has nothing to do with procurement and buys nothing, so the
scheduler now runs this file directly and the agent's only job is to read the
result afterwards.

Usage:
    python scraper-service/run_sweep.py                 # both sources
    python scraper-service/run_sweep.py --source ted-eu # one
    python scraper-service/run_sweep.py --limit 500     # bounded, for a smoke test
    python scraper-service/run_sweep.py --dry-run       # no writes, no embeddings

Exit codes are meant to be read by a scheduler:
    0  every source finished and wrote what it found
    1  ran, but at least one source was partial or aborted, the embedding
       budget ran out, or the corpus gained no new tenders at all
    2  could not start (missing configuration, unreachable database)

Nobody reads an exit code, so each one is also said out loud:
    * exit 1 -- the operator digest, whose subject names what went wrong;
    * exit 2 -- a last-ditch alarm email, since no digest is ever built;
    * the machine is off, asleep, or the task never fired -- nothing on this
      machine can report that. Set HEARTBEAT_URL to a dead-man's-switch check
      (healthchecks.io, or anything that accepts `<url>/start` and
      `<url>/<exit code>`) and it will complain when the pings stop.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import Optional
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))


def build_logger(log_path: Path) -> logging.Logger:
    """Log to the file AND to stdout.

    The file is what a scheduler keeps; stdout is what a human sees when they
    run it by hand. Writing only one of them means the run is either invisible
    now or invisible later.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S")

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()

    for handler in (logging.FileHandler(log_path, encoding="utf-8"),
                    logging.StreamHandler(sys.stdout)):
        handler.setFormatter(fmt)
        root.addHandler(handler)

    # These three are chatty enough to bury the run's own story.
    for noisy in ("httpx", "httpcore", "urllib3", "hpack", "google", "google_genai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    return logging.getLogger("sweep")


def _post(url: str, body: str) -> None:
    import httpx
    httpx.post(url, content=body.encode("utf-8"), timeout=10)


def heartbeat(signal, body: str = "") -> None:
    """Ping the dead-man's switch, if one is configured. Never raises.

    `signal` is "start" or an exit code. The check turns red on a non-zero
    code, and -- the reason it exists -- on silence, which is the one failure
    the run itself can never report. Read from the environment directly,
    not from Settings, because exit 2 is often Settings failing to load.
    """
    url = (os.environ.get("HEARTBEAT_URL") or "").strip().rstrip("/")
    if not url:
        return
    try:
        _post(f"{url}/{signal}", body)
    except Exception as exc:  # noqa: BLE001 - the ping reports the job; it is not the job
        logging.getLogger("sweep").warning("heartbeat ping failed: %s", exc)


def send_alarm(subject: str, body: str) -> None:
    """Best-effort email for the run that never got far enough to digest."""
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
        from app.config import Settings
        from app.notifications import EmailNotifier
        EmailNotifier(Settings()).send_operator_alarm(subject, body)
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("sweep").error("could not send the alarm either: %s", exc)


def _user() -> str:
    try:
        import getpass
        return getpass.getuser()
    except Exception:  # noqa: BLE001 - no login name is itself the answer
        return "?"


def browser_hint(exc: Exception) -> Optional[str]:
    """The thing to go and change when Playwright cannot find its browser.

    Put in the digest next to the error, because the error alone ("does not
    exist at <path>") was read for five nights as a broken install, when the
    install was fine and the account looking for it was not the one that
    installed it.
    """
    if "Executable doesn't exist" not in str(exc):
        return None
    where = os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or "(unset -- per-user default)"
    return (
        f"hint: the sweep ran as '{_user()}' with PLAYWRIGHT_BROWSERS_PATH={where}. "
        "Run it through run_sweep.cmd, which pins the browsers inside the project "
        "and installs them if missing; a task pointing at run_sweep.py directly "
        "looks in the profile of whichever account it runs as."
    )


def count_tenders(settings, since: datetime) -> tuple[Optional[int], Optional[int]]:
    """(total, created since `since`), read back from the database.

    The second number is the one HANDOFF called the signal nobody watches:
    the counters can say `ingested 900` for rows that were already there, or
    that a trigger then refused. Only the table knows whether the corpus grew.
    """
    from app.storage.supabase_repo import SupabaseRepository
    client = SupabaseRepository(settings).client
    total = client.table("tenders").select("id", count="exact").limit(1).execute().count
    new = (
        client.table("tenders").select("id", count="exact")
        .gte("created_at", since.isoformat()).limit(1).execute().count
    )
    return total, new


async def run(args, log: logging.Logger) -> int:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")

    from app.config import Settings
    from app.pipeline import IngestionPipeline
    from app.agents import get_agent

    settings = Settings(
        dry_run=args.dry_run,
        extract_on_ingest=False,      # listing-first: no model call per tender
        ted_open_only=True,           # open tenders, not recent ones
        ted_divisions_per_query=1,    # one resumable walk per CPV division
    )

    # Who and where, every run. mod-balam failed for five nights on a browser
    # that existed -- for a different account than the one the task ran as,
    # most likely -- and nothing in the log could say which account that was.
    log.info("runtime   : user=%s python=%s", _user(), sys.executable)
    log.info("browsers  : %s", os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
             or f"(default) {os.environ.get('LOCALAPPDATA', '?')}\\ms-playwright")
    log.info("sources   : %s", ", ".join(args.source))
    log.info("limit     : %d per source", args.limit)
    log.info("dry run   : %s", settings.dry_run)
    log.info("embed     : %s, batch<=%d, <=%d tok/req, %d tok/min",
             settings.embedding_provider, settings.embed_batch_size,
             settings.embed_max_tokens_per_request, settings.embed_tokens_per_minute)

    pipeline = IngestionPipeline(settings=settings)
    sweep_started = datetime.now(timezone.utc)

    # Read the tenders somebody matched, BEFORE harvesting more.
    #
    # `extract_matched()` was written with listing-first ingestion (ADR-22)
    # and then called by nothing: 5,645 tenders sat listing-only, including
    # every one a customer had been alerted to. It goes first because the
    # harvest spends whatever embedding budget it is given -- uk-fts is
    # starved every night -- so placed after it this step would never get a
    # request. Capped by EXTRACT_MATCHED_LIMIT, and skipped on a dry run
    # because extraction is spend, not a write, and a dry run should cost
    # nothing.
    if not settings.dry_run and settings.extract_matched_limit > 0:
        try:
            extracted = await pipeline.extract_matched()
            log.info("extracted %d matched tender(s) before harvesting", extracted)
        except Exception as exc:  # noqa: BLE001
            # Not the harvest's failure, and must not cost the harvest.
            log.warning("could not extract matched tenders: %s", exc)

    worst = 0
    # Kept, not just printed. The per-source report was logged and dropped,
    # which was fine while nothing downstream wanted it; the operator digest
    # is built from the whole set.
    reports = []
    for key in args.source:
        agent = get_agent(key, settings)
        started = time.time()
        try:
            report = await pipeline.run_source(agent, limit=args.limit)
        except Exception as exc:  # noqa: BLE001
            # One source failing must not cost the other one. A sweep that
            # got TED and lost the UK is a partial sweep, not a dead one.
            log.exception("[%s] aborted: %s", key, exc)
            worst = max(worst, 1)
            # Kept, so the digest names it. Dropping it here made the source
            # that failed the one source the digest did not mention.
            from app.pipeline import IngestReport
            errors = [f"aborted: {type(exc).__name__}: {exc}"]
            hint = browser_hint(exc)
            if hint:
                errors.append(hint)
            reports.append(IngestReport(
                source_key=key, aborted=True, partial=True, errors=errors,
            ))
            continue

        reports.append(report)

        minutes = (time.time() - started) / 60
        log.info("=== %s finished in %.1f min ===", key, minutes)
        log.info("    found    %d", report.found)
        log.info("    ingested %d", report.ingested)
        log.info("    skipped  %d", report.skipped)
        log.info("    failed   %d", report.failed)
        if report.unattempted:
            log.info("    UNATTEMPTED %d -- the run stopped before reaching them",
                     report.unattempted)
        log.info("    partial  %s", report.partial)
        for err in report.errors[:5]:
            log.info("    err: %s", err[:200])

        if report.partial or report.failed:
            worst = max(worst, 1)

    # Everything the sweep owes once the sources are done: age the corpus,
    # deliver the customer alerts, send the operator digest.
    #
    # This call is the whole of a defect that lasted as long as this script
    # has existed. All three of those live at the end of `pipeline.run()`,
    # and this script does not call `run()` -- it drives `run_source()` in
    # the loop above so it can order the sources by priority and report each
    # one on its own. So none of them happened here. The scheduled sweep,
    # every morning, harvested tenders, wrote them, let the trigger match
    # them against customer profiles, and told nobody.
    #
    # On 2026-09-18 that was 31 matches waiting, next to 79 that had been
    # delivered only because someone asked for them by hand, one batch at a
    # time. Nothing was broken and nothing was missing: the alerting worked,
    # and the morning run did not reach it.
    #
    # A failed delivery counts against the exit code. "Ran, but did not
    # finish" is what exit 1 is for, and a sweep whose alerts did not leave
    # the building has not finished the job this product is sold to do.
    # The only numbers that say whether this worked. Read back from the
    # database rather than trusted from the counters above: a sweep can
    # report `ingested` for rows a trigger then refused. Read BEFORE the
    # digest, so the digest can say "the corpus did not grow".
    new_tenders = None
    if not settings.dry_run:
        try:
            total, new_tenders = count_tenders(settings, sweep_started)
            log.info("DATABASE NOW HOLDS %s TENDERS (%s new this run)", total, new_tenders)
        except Exception as exc:  # noqa: BLE001
            log.warning("could not read the tender count back: %s", exc)
        if new_tenders == 0:
            log.warning("NO NEW TENDERS -- the corpus did not grow")
            worst = max(worst, 1)

    if new_tenders is None:
        finished = await pipeline.finish_sweep(reports)
    else:
        finished = await pipeline.finish_sweep(reports, new_tenders=new_tenders)
    if not finished:
        worst = max(worst, 1)

    return worst


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", action="append",
                        choices=["ted-eu", "uk-fts", "mod-balam"],
                        help="repeatable; defaults to ted-eu and uk-fts")
    parser.add_argument("--limit", type=int, default=20000,
                        help="max tenders per source (default 20000)")
    parser.add_argument("--dry-run", action="store_true",
                        help="fetch and filter, write nothing")
    parser.add_argument("--log-dir", default=str(ROOT / "logs"))
    args = parser.parse_args()
    # Israel first, deliberately, and not only for completeness.
    #
    # This defaulted to ["ted-eu", "uk-fts"] and the Israeli MoD list -- the
    # source this product is sold against first -- was simply not in it. It
    # went unswept for five days while the daily job reported success every
    # morning. Measured on the sixth: 204 BALAM tenders held, only 98 still
    # open, and 166 open on the live portal. A third of the Israeli market
    # invisible, with nothing failing.
    #
    # It runs FIRST because the embedding budget is finite and the earlier
    # sources spend it. When the budget runs out mid-sweep the run aborts,
    # and whatever is last in this list gets nothing -- which is exactly
    # what happened to uk-fts today. Order here is a priority, not a
    # sequence, and the home market outranks the volume.
    args.source = args.source or ["mod-balam", "ted-eu", "uk-fts"]

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    log_path = Path(args.log_dir) / f"sweep-{stamp}.log"
    log = build_logger(log_path)
    log.info("log file  : %s", log_path)
    heartbeat("start")

    try:
        code = asyncio.run(run(args, log))
    except KeyboardInterrupt:
        log.warning("interrupted")
        code = 1
    except Exception as exc:  # noqa: BLE001
        log.exception("could not start: %s", exc)
        # No digest is built on this path, so without this the run ends
        # without a word to anyone.
        send_alarm(
            "[Tenders Finder] Sweep could not start",
            "The scheduled sweep exited 2 before harvesting anything.\n\n"
            f"{type(exc).__name__}: {exc}\n\nLog: {log_path}",
        )
        code = 2

    log.info("exit %d", code)
    heartbeat(code, _tail(log_path))
    return code


def _tail(path: Path, limit: int = 10_000) -> str:
    """The end of the log, so the heartbeat check shows why, not only that."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")[-limit:]
    except OSError:
        return ""


if __name__ == "__main__":
    raise SystemExit(main())
