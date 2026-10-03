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
| Supabase | `vixuawfbayglbgitzqbj`, Frankfurt, **free plan**, 102 MB used |
| Tenders | **5,645** — every one carrying an embedding |
| Open for bids | **4,786** |
| Buying countries | **37** |
| Live sources | 3 configured, **2 contributing**: `mod-balam` broken since 17/09, `uk-fts` finds ~2,250 a night and ingests 0 (starved, not broken) |
| Matches | 138 — **all alerted**, 0 pending |
| Tests | **332**, ~12s, no network and no API keys |
| Scheduled | 3 pg_cron jobs + a Windows Task Scheduler sweep at 10:05 |
| Dashboard | **live**, `https://tenders-finder-app.vercel.app` (deployed 18/09) |
| Landing page | live, Hebrew + English, `https://tenders-finder.vercel.app` |
| Known-good state | commit `069e687`, tag + branch `handoff-2026-09-22` |

The product has run end to end: harvest → filter → embed → match → email.
**121 alerts have actually been delivered.** Cross-language matching is not a
claim — an English capability profile has matched notices published in
Greek, Hebrew, Swedish and German.

Since 18/09 the sweep finishes itself: it delivers customer alerts, sends the
operator digest, and exits non-zero when a source failed. Verified running
unattended on 19/09.

---

## Two clocks, running now (written 22/09)

Nobody is at the machine for a month. These are not hypotheses; both numbers
were measured, and the second is quoted from Supabase's own documentation.

**Clock 1 — the database fills up in about 24 days.** 102 MB used, ~19 KB per
tender (the 1024-dim vector plus its HNSW index), 960 tenders ingested every
day, and the free plan's ceiling is 500 MB. Supabase: *"Free Plan projects
enter read-only mode when your database size exceeds 500 MB."*

Read-only has a consequence worth stating plainly, because it is worse than a
stalled harvest: `mark_matches_alerted` is a **write**. An alert email would
go out, the stamp would fail, the match would stay pending — and the same
alert would be sent again the next day, and the day after. To customers.

**Clock 2 — the project is paused after 7 days of low activity.** Supabase:
*"We may pause applications on the Free Plan that exhibit low activity in a
7-day period."* Restorable from the dashboard, but silent until someone looks.

**The two are mutually exclusive, which is the point.** If the machine keeps
running, clock 1 wins. If it is switched off, clock 2 does. Within a month,
one of them fires either way.

The free plan also gives **no restorable backup**: *"Database backups are not
available for download for Free Plan projects."* Point-in-time recovery is a
paid add-on. **The export taken on 22/09 is the only restore path that
exists** — see the backup folder's `RESTORE.md`.

Deciding what to do about this was deliberately left to whoever picks the
project up. Upgrading to Pro (~$25/month) buys all three: 7 days of daily
backups, 8 GB instead of 500 MB, and a guarantee against the inactivity
pause. Staying on free is a legitimate choice; not knowing about the clocks
is not.

---

## Blocked on the user

**1. The embedding budget is the ceiling.** Gemini's free tier allows
**1,000 embedding requests per day**. That is enough to keep growing but not
enough for a daily refresh of tens of thousands of tenders plus customers.
The options are a paid Gemini tier (cents, and it changes nothing else) or a
`VOYAGE_API_KEY` — but Voyage means **re-embedding the whole corpus**,
because vectors from two models are not comparable. That is a decision, not
a quick fix.

**2. A CI commit cannot be pushed.** GitHub refuses a workflow change from an
OAuth App without `workflow` scope. The commit exists, on the branch
`claude/handoff-md-review-979716` and in the backup bundle: it adds two
landing-page checks to CI — `npm run check:en`, and a regeneration check that
rebuilds `en/index.html` and diffs it, which is the one that catches an
English page shipped a version behind. Either paste the file into GitHub's
web editor, or issue a token with `workflow` scope. Do not rewrite history to
work around it; do not make the repository public to work around it.

**Branch protection is not enabled.** `main` and `backup/handoff-2026-09-22`
both sit at the known-good commit `069e687`, and nothing stops a force push
from erasing either. Settings → Branches → Add rule, then block force pushes
and deletions. This matters more than usual while people who did not build it
are working on it.

**3. There is no customer.** The system has never run for anyone outside the
team that built it. There is a sign-up form, a trigger that provisions an
organisation, a working engine and working alerts — and not one company that
received an alert and said whether it was worth anything. Everything else in
this file is work. This is the question.

---

## The work queue

Ordered by what is broken rather than by what is interesting.

1. **`mod-balam` has harvested nothing since 17/09.** Five consecutive nights:
   `BrowserType.launch: Executable doesn't exist at
   …chromium_headless_shell-1148\chrome-win\headless_shell.exe`. This is the
   Israeli MoD list — the home market, and the source this product is sold
   against first. **The clue:** that executable *does* exist and runs
   correctly when launched by hand from an interactive shell (verified,
   Chromium 131.0.6778.33). So `playwright install chromium` may not be the
   fix; the difference between that shell and the scheduled task is where to
   look. The run now exits 1 instead of reporting success, which is the only
   reason this is visible at all.
2. **Nothing watches the exit code.** A failed source makes the sweep exit 1
   and send a digest, and no alarm reads either. `added_today = 0` is the
   other signal nobody watches. This is the cheapest work on the list and it
   is what turns every future silent failure into a loud one.
3. **Password reset and the email-link landing are built (03/10), not yet
   live until two dashboard settings are made.** `/forgot-password`,
   `/reset-password`, and `/auth/callback` (PKCE `?code=`, plus
   `?token_hash=` for edited templates). Sign-up and the login page's new
   "send the confirmation again" button both target the callback; before,
   confirmation links went to the Site URL, which had no handler. **Operator
   step:** in the live project (`vixuawfbayglbgitzqbj`) set Authentication ->
   URL Configuration -> Site URL to `https://tenders-finder-app.vercel.app`
   and add `https://tenders-finder-app.vercel.app/auth/callback` and
   `http://localhost:3000/auth/callback` to Redirect URLs. A target not on
   that list is silently replaced by the Site URL. Known limit: a PKCE link
   only works in the browser that requested it; switching the email
   templates to `{{ .TokenHash }}` removes that with no code change.
4. **Run the deferred extraction.** `tenders_awaiting_extraction()` exists and
   is scheduled nowhere. 5,645 tenders are in the corpus listing-only; the
   ones somebody actually matched are the cheap and valuable subset.
5. **Onboarding is a SQL insert.** An alert profile — the paragraph that
   decides what a customer is shown — is written by hand. Until there is a
   screen, every new customer needs whoever holds the service-role key.
6. **`uk-fts` is starved, not broken.** ~2,250 notices found a night, 0
   ingested, because TED spends the day's embedding budget first. The order is
   deliberate (the home market outranks the volume). The fix is budget.
7. **BALAM matches on 27 characters.** TED and FTS carry a full notice; the
   Israeli MoD list publishes a title and nothing else. Shallow by
   construction, and no fix without supplier access.
8. **`govil` stays disabled** — see the constraints.

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
