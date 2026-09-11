# Where this stopped — 2026-09-11

Written at a deliberate pause, not at a finish line. Everything below is
measured, not assumed.

## Blocked on you, first thing

**The five commits are pushed.** `main` is in sync with `origin/main`.

**One file is not, and it is the one keeping CI red: `.github/workflows/ci.yml`.**
GitHub refuses a workflow change from an OAuth App without `workflow` scope, so
I rewrote the five commits to leave that file exactly as the remote has it and
pushed the rest. The only difference between what was pushed and what I built
is that one file — verified, not assumed.

The full, final version of it is parked on a local branch. Two commands:

```bash
git checkout ci-workflow-fix -- .github/workflows/ci.yml
git commit -m "Teach CI to check the rule, not one filename" && git push origin main
```

**Until that lands, CI is red on `main`** — and it was already red before this
session's pushes: `295720e` deleted `landing/favicon.svg` while the workflow
still asserts the file exists. That assertion is exactly what the parked
version replaces: it now checks that no traced logo survives in the page and
that both brand marks point at the real one, which is the rule the old check
was standing guard over and could never actually enforce.

## State of the system

| | |
|---|---|
| Supabase | healthy (was auto-paused after a week; you resumed it) |
| Tenders | ~180, all extracted and embedded |
| Status column | correct — 0 rows disagree with their own deadline |
| Tests | **190 passing** |
| Landing page | live, real logo, robots/sitemap/llms.txt/404 |
| CI | **red on `main` until the push above lands** |

## What changed this session

**Tender lifecycle (0011).** `status` had defaulted to `'open'` since 0003 and
nothing ever moved it: 168 rows, all `open`, 46 with a deadline already past.
`age_tender_status()` derives it from `deadline`, is idempotent and
self-correcting, and never touches `awarded`/`cancelled`. Applied; verified by
invariant, not by count.

**Partial harvests no longer read as complete.** A sweep stopped at page 4 of
10, took 60 of 185 tenders, and reported `found=60 ingested=60 errors=[]`. The
code was fine — the agent walks all ten pages in 0.5–2.7s each, proven twice
live. One postback did not answer, once. Fixed: one retry, and `partial`
travels into the report and leads the digest subject.

**Incremental extraction.** Sweeps re-fetched and re-extracted everything.
Now skipped when the listing row, prompt version and model all match; anything
unknown re-extracts.

**`/health` and `/extract`** were still gated on Anthropic while the service
runs Gemini. `/health` announced `claude-opus-5`; `/extract` returned 503.

## Pick up here

1. **Land the workflow file** — the two commands above. Nothing else should
   start before CI is green.
2. **Finish a full sweep.** The last one was stopped mid-run by hand, not by a
   failure. Run it and confirm the report shows `skipped` high and `partial`
   false.
3. **Schedule `age_tender_status()`.** `pg_cron` is available in the project
   and not installed. Without a schedule the function runs only when a sweep
   runs, which is the assumption ADR-21 says not to rely on.
   ```sql
   create extension pg_cron;
   select cron.schedule('age-tenders', '0 * * * *',
     $$select public.age_tender_status()$$);
   ```
4. **Automated sweeps.** Still manual. This is the last thing standing between
   the MVP and the product it claims to be.

## Known, not yet touched

- Two pager-artefact rows (`external_id` "1" and "2") still in `tenders`.
- Six CI env-pin lines need a token with `workflow` scope.
- `scraper-service/tests/fixtures/` is gitignored (holds real phone numbers).
- A stray `WhatsApp Image ... .jpeg` sits untracked in the repo root; the same
  file is committed properly as `brand/logo-source.jpeg`. Safe to delete.

## The rule this session added to REPOMIX

> A guard that stops is worth nothing if the reason it stopped does not reach
> whoever reads the result. Stopping quietly is still failing quietly.
