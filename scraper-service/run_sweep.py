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
    1  ran, but at least one source was partial or the embedding budget ran out
    2  could not start (missing configuration, unreachable database)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from datetime import datetime, timezone
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

    log.info("sources   : %s", ", ".join(args.source))
    log.info("limit     : %d per source", args.limit)
    log.info("dry run   : %s", settings.dry_run)
    log.info("embed     : %s, batch<=%d, <=%d tok/req, %d tok/min",
             settings.embedding_provider, settings.embed_batch_size,
             settings.embed_max_tokens_per_request, settings.embed_tokens_per_minute)

    pipeline = IngestionPipeline(settings=settings)

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
    if not await pipeline.finish_sweep(reports):
        worst = max(worst, 1)

    # The only number that says whether this worked. Read back from the
    # database rather than trusted from the counters above: a sweep can
    # report `ingested` for rows a trigger then refused.
    if not settings.dry_run:
        try:
            from app.storage.supabase_repo import SupabaseRepository
            repo = SupabaseRepository(settings)
            result = repo.client.table("tenders").select(
                "id", count="exact"
            ).limit(1).execute()
            log.info("DATABASE NOW HOLDS %s TENDERS", result.count)
        except Exception as exc:  # noqa: BLE001
            log.warning("could not read the tender count back: %s", exc)

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

    try:
        code = asyncio.run(run(args, log))
    except KeyboardInterrupt:
        log.warning("interrupted")
        return 1
    except Exception as exc:  # noqa: BLE001
        log.exception("could not start: %s", exc)
        return 2

    log.info("exit %d", code)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
