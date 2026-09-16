# Handoff — picking up Tenders Finder

Written for an assistant starting a fresh session in this repository, and
kept current rather than appended to. There used to be two of these:
`HANDOFF.md` from 01/09 and `HANDOFF-NEXT.md` from 11/09. They disagreed
with each other and both disagreed with the database — one said 180 tenders
and 190 tests, the other said "nothing schedules the scraper". Two handoffs
is how a handoff becomes archaeology, so there is one, and every number in
it was read back from the live system on **2026-09-15**.

**Read `AGENTS.md` first** — the behaviour contract, and authoritative over
anything here. (`CLAUDE.md` is a pointer to it; one contract, two doors.) Then `REPOMIX.md`, the knowledge base in Hebrew: vision,
stack rationale, **26 architecture decision records**, and a table of silent
failures that is the single most useful page in this repository.

The user is Israeli and writes in Hebrew. **Reply in Hebrew.** Code,
comments, commit messages and documentation stay in English — that split is
consistent across the repository; keep it.

---

## State, measured

| | |
|---|---|
| Repository | `omer-izhaki/tenders-finder`, private, default branch `main` |
| Supabase | `vixuawfbayglbgitzqbj`, Frankfurt, free tier, healthy |
| Tenders | **2,751** — every one carrying an embedding |
| Open for bids | **2,329** |
| Buying countries | **37** |
| Live sources | **2** (Israeli MoD, TED/EU) + UK Find a Tender written and tested, waiting on budget |
| Matches | 81 — 66 alerted, 15 skipped as too late, **0 pending** |
| Tests | **320**, ~23s, no network and no API keys |
| Scheduled | 3 pg_cron jobs + a Windows Task Scheduler sweep at 10:05 |
| Landing page | live, Hebrew + English, `https://tenders-finder.vercel.app` |

The product has run end to end: harvest → filter → embed → match → email.
**66 alerts have actually been delivered.** Cross-language matching is not a
claim — an English capability profile has matched notices published in
Greek, Hebrew, Swedish and German.

---

## Blocked on the user

**1. The embedding budget is the ceiling.** Gemini's free tier allows
**1,000 embedding requests per day**. That is enough to keep growing but not
enough for a daily refresh of tens of thousands of tenders plus customers.
The options are a paid Gemini tier (cents, and it changes nothing else) or a
`VOYAGE_API_KEY` — but Voyage means **re-embedding the whole corpus**,
because vectors from two models are not comparable. That is a decision, not
a quick fix.

**2. Six CI lines cannot be pushed.** GitHub refuses a workflow change from
an OAuth App without `workflow` scope. Two checks written this week are not
in `.github/workflows/ci.yml` on the remote: the coverage-stat check
(`data-stat`) and the English-translation check (`npm run check:en`). Either
the user pastes them into GitHub's web editor, or they issue a token with
`workflow` scope. Do not rewrite history to work around it; do not make the
repository public to work around it.

**3. There is no customer.** The system has never run for anyone outside the
team that built it. There is a sign-up form, a trigger that provisions an
organisation, a working engine and working alerts — and not one company that
received an alert and said whether it was worth anything. Everything else in
this file is work. This is the question.

---

## The work queue

1. **Finish the harvest.** ~36,700 open tenders are reachable across TED's
   31 CPV divisions and UK Find a Tender; 2,751 are in. At 96 per request
   that is ~400 requests — a third of one day's free budget — but the day it
   belongs to has to start unspent. The scheduled sweep runs at 10:05 and
   now stops and says so when the budget is gone.
2. **Run the deferred extraction.** `tenders_awaiting_extraction()` exists,
   is scheduled nowhere, and currently holds **39** tenders — ones somebody
   matched and nobody has read. This is the cheap half: 39 documents, not
   2,751.
3. **A manual review screen.** `needs_review` is 0 today. When it is not,
   there is nowhere to look at it.
4. **BALAM matches on 27 characters.** TED and FTS carry a full notice; the
   Israeli MoD list publishes a title and nothing else. Matching there is
   shallow by construction and there is no fix without supplier access.
5. **`govil` stays disabled** — see the constraints.

---

## Constraints that are not negotiable

Each was decided for a reason and re-deciding them is not in scope.

1. **Do not lift the gov.il front end's embedded `x-client-id`.** The gateway
   rejects requests without it, which makes it a credential rather than a
   public constant. The route is requesting our own key from the operator.
2. **`sapakim.mod.gov.il` is off limits.** It serves a genuine
   `User-agent: * / Disallow: /`. Re-confirmed after a robots-parsing bug was
   fixed, so it is policy, not artefact.
3. **The Supabase service-role key bypasses RLS entirely.** It lives in the
   root `.env` for the scraper and nowhere else — never in `web/`, never
   behind `NEXT_PUBLIC_`, never in a commit.
4. **Never persist unvalidated LLM output.** Validate against the Pydantic
   model; on failure retry once with the errors fed back; on a second failure
   route to `needs_review`. A `needs_review` tender is deliberately left
   unembedded so it raises no alerts — do not "fix" that by embedding it.
5. **Secrets never go in chat or in commits.** The user has pasted a live
   credential into a conversation before. If it happens again: tell them to
   revoke it, do not use it, do not write it anywhere.
6. **Check terms of use and `robots.txt` before enabling any portal.**
7. **Never edit an applied migration.** They are numbered and applied in
   order. A correction is a new file.

### The Supabase MCP server, and why it is read-only

```
claude mcp add supabase --env SUPABASE_ACCESS_TOKEN=<pat> -- \
  npx -y @supabase/mcp-server-supabase@latest --read-only \
  --project-ref=vixuawfbayglbgitzqbj
```

Both flags are deliberate:

- `--project-ref` — the token is scoped to the **whole Supabase account**.
  Without this the server reaches every project the user has, now and later.
- `--read-only` — this database is full of **text scraped from government
  portals**: content neither the user nor the assistant wrote, and then read
  back. That is exactly the path by which instructions injected into data
  become a problem.

The cost is real: **you cannot write to the database.** Migrations and data
fixes go through the SQL Editor, where the user sees the statement before it
runs. Give them the SQL. Do not ask for the flag to be dropped.

---

## How this project expects you to work

Taken from `AGENTS.md`, and from what has actually caught bugs here.

**Verify, do not assume.** Nearly every real defect on this project was
found by running something, not by reading it. A probe found TED returns a
continuation token only if you ask for it. A browser found the landing page
had no `<!DOCTYPE>` and was rendering in quirks mode. A dashboard screenshot
found the binding limit was tokens per minute, not requests.

**A passing test that proves nothing is worse than no test.** This week two
defects shipped behind green tests, because the tests asserted the payload
the author had imagined rather than the one the API sends. When a test
covers an external response, **paste the real body**.

**The failure mode here is silence, not noise.** `REPOMIX.md` keeps a table
of every silent failure found on this project, and the pattern is always the
same: counts agree, rows look right, nothing errors, and the work was not
done. A sweep that harvested 12,000 notices and wrote zero rows reported
itself as running. An audit query raised a false alarm because it forgot one
`where` clause. **When a number looks plausible, that is when to check it.**

**Read the result back from the system, not from the log.** A log full of
"harvested N" with zero rows written is a failed run. Harvesting is free;
writing is what costs.
