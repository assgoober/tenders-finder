# Handoff — starting a local Claude Code session on this project

Paste everything below the line into a fresh `claude` session started inside
this repository. It is written to be read by the assistant, not by a person.

---

## Who you are and what this is

You are picking up **Tenders Finder**, a B2G/B2B tender-aggregation platform
for Israeli defence contractors. It aggregates public procurement tenders
scattered across isolated government portals into one workspace: scraping
agents, schema-constrained AI extraction, semantic matching against a company
profile, and a dashboard that replaces portal-by-portal searching.

**Read these two files first, before doing anything else:**

- `CLAUDE.md` — the behaviour contract. Architecture rules, done-criteria,
  and the constraints below in their authoritative form.
- `REPOMIX.md` — the full knowledge base, in Hebrew. Vision, stack rationale,
  17 architecture decision records, file map, open risks. **ADR-17 explains
  why this repository exists separately.** If you make an architectural
  decision, add an ADR here; `CLAUDE.md`'s done-criteria require it.

The user is Israeli and writes in Hebrew. Reply in Hebrew. Code, comments,
commit messages and documentation stay in English — that split is already
consistent across the repository, keep it.

## Exact state as of this handoff

| | |
|---|---|
| Repository | `omer-izhaki/tenders-finder`, **private**, default branch `main` |
| HEAD | `19b9182` — "Read live data from Supabase, behind authentication" |
| CI | Green on all three jobs (`scraper-service`, `web`, `landing page`) |
| Supabase project | `vixuawfbayglbgitzqbj`, region Frankfurt, free tier |
| Landing page | Live at `https://tenders-finder.vercel.app` |

**A second Claude session is working on this repository at the same time**
(Claude Code on the web, in a cloud container). Read "Working in parallel"
below before your first push — this is the part most likely to go wrong.

### What is done

- **Schema applied and verified live.** All five migrations ran against the
  real Supabase project. `supabase/verify.sql` returns 8/8 `OK` there:
  9 tables, RLS enabled on all and forced on the 5 tenant-scoped ones,
  11 policies, 7 project functions, 2 HNSW indexes, the alert trigger,
  pgvector.
- **Cross-organization isolation tested, not assumed.** Against PostgreSQL 16
  with pgvector: two organizations, one alert profile each. Each member saw
  only their own; a member could not insert into the other organization even
  naming its id explicitly; a signed-in user in no organization saw nothing.
- **Dashboard reads live data behind authentication.** `lib/api.ts`,
  `lib/supabase/{server,client,config}.ts`, `middleware.ts`,
  `app/login/page.tsx`, `app/auth/signout/route.ts`.
- **Landing page** deployed, WCAG 2.2 AA / IS 5568, RTL.
- **Scraper service**: 143 tests pass with no network and no API keys.
  `mod-balam` is verified against the live portal and enabled.

### What is not done — the actual work queue

1. **No user account exists.** Nothing can be seen in the dashboard until one
   does. Create it in the Supabase dashboard (Authentication → Users → Add
   user), then run the organization + membership SQL in
   `supabase/README.md`. **Only the user can do the first half**; you cannot
   reach their Supabase dashboard.
2. **The database has no tenders.** The scraper has never written to it. This
   is the main event: run the pipeline against the live project and get real
   MOD tenders in.
3. **`ANTHROPIC_API_KEY` and `VOYAGE_API_KEY` are unset.** Without Voyage,
   embeddings fall back to `EMBEDDING_PROVIDER=hash`, which is deterministic
   but **not semantic** — matching results will be junk. Say so plainly
   rather than letting a hash-provider run look like a working matcher.
4. **`govil` agent is disabled** and stays that way (see constraints).
5. **Nothing schedules the scraper.** There is no cron, no worker. `/ingest`
   is manual.
6. **`web/.env.local` does not exist.** Next reads env from `web/`, not the
   repository root, so the root `.env` the scraper uses is invisible to it.
   `NEXT_PUBLIC_*` values are inlined **at build time**, so a build made
   without them serves demo fixtures no matter what the runtime environment
   holds later.

## What you can do that the cloud session cannot

This is why the user moved here. The cloud container cannot reach their
machine, their browser, or their Supabase dashboard. You can:

- Create `web/.env.local` and actually run `npm run dev`, then open the
  dashboard in a browser and look at it.
- Run the scraper against the live database.
- Query Supabase directly through the MCP server (see below) instead of
  asking the user for screenshots.
- Run Playwright against the live portals from an Israeli IP. **This matters
  more than it sounds**: the portals may geo-restrict, and the cloud session
  runs from elsewhere.

## The Supabase MCP server

Configured, or to be configured, as:

```
claude mcp add supabase --env SUPABASE_ACCESS_TOKEN=<pat> -- \
  npx -y @supabase/mcp-server-supabase@latest --read-only \
  --project-ref=vixuawfbayglbgitzqbj
```

Both flags are deliberate and **you should not suggest removing them
casually**:

- `--project-ref` — the token is a **personal access token scoped to the
  whole Supabase account**, not to one project. Without this flag the server
  reaches every project the user has, now and in future.
- `--read-only` — this database is about to fill with **text scraped from
  government portals**: content neither the user nor you wrote, which you
  will then read. That is the exact path by which instructions injected into
  data become a problem. Read-only is the difference between an agent reading
  something strange and an agent dropping a table.

The cost is real and you should work with it, not around it: **you cannot
write to the database.** Migrations, the organization insert, and any data
fix go through the Supabase SQL Editor, where the user sees the statement
before it runs. Give them the SQL; do not ask for the flag to be dropped
unless they raise it first.

## Working in parallel with the other session

Both sessions push to `main` of the same repository. Assume the remote has
moved since you last looked.

**Before any work:** `git pull --rebase origin main`
**Before any push:** `git pull --rebase origin main`, re-run the checks, then
push. Never `git push --force` to `main`.

**Ownership, to keep you out of each other's files:**

| Area | Owner |
|---|---|
| `scraper-service/` runtime, live portal runs, `.env` | **you** (local) |
| `web/` running, `web/.env.local`, browser verification | **you** (local) |
| Supabase — reads via MCP, SQL handed to the user | **you** (local) |
| `landing/`, `REPOMIX.md`, `CLAUDE.md`, `.github/` | cloud session |
| `supabase/migrations/` | whoever writes it, but **announce it** |

If you need to touch something outside your column, say so in your reply so
the user can relay it. A migration written twice is far worse than a
migration delayed — migrations are numbered, applied in order, and must never
be edited once applied.

## Constraints that are not negotiable

These were each decided for a reason and re-deciding them is not in scope.

1. **Do not lift the gov.il front end's embedded `x-client-id`.** The Apigee
   gateway rejects requests without it, which makes it a credential rather
   than a public constant. Using it would put this integration on someone
   else's quota and identity. The route is requesting our own key from the
   API operator. `govil` stays disabled until then.
2. **`sapakim.mod.gov.il` is off limits.** It serves a genuine
   `User-agent: * / Disallow: /`. No agent, no probe beyond the robots check,
   no workaround. This was re-confirmed after a robots-parsing bug was fixed,
   so it is policy, not artefact.
3. **The Supabase service-role key bypasses RLS entirely.** It belongs in
   `.env` for the scraper service and nowhere else — never in `web/`, never
   behind a `NEXT_PUBLIC_` prefix, never in a commit.
4. **Never persist unvalidated LLM output.** Validate against the Pydantic
   model; on failure retry once with the errors fed back; on a second failure
   route to `needs_review`. A `needs_review` tender is **deliberately left
   unembedded** so it raises no alerts — do not "fix" that by embedding it.
5. **Secrets never go in chat or in commits.** The user has previously pasted
   a live credential into a conversation. If it happens again, tell them to
   revoke it immediately, do not use it, and do not write it to any file.
   PowerShell history is written to disk, so secrets go into `.env` through
   an editor, not typed as commands.
6. **Check terms of use and `robots.txt` before enabling any portal.**
7. **Do not make the repository public** to work around a tooling
   permission problem.

## How this project expects you to work

Taken from `CLAUDE.md` and from what has actually caught bugs here:

- **Verify, do not assume.** Nearly every real defect on this project was
  found by running something, not by reading it: CI found that `pytest -q` —
  the command both the README and `CLAUDE.md` documented — had never worked;
  a browser found the landing page had no viewport meta at all; introspecting
  the installed SDK found `requirements.txt` floored `anthropic` at a version
  where the `fallbacks` feature did not exist.
- **A passing test that proves nothing is worse than no test.** Two checks
  here returned green while being invalid — a stale server on the port served
  a pre-fix build, and an RLS isolation test "passed" because the fixture
  inserts had silently failed and the table was empty. An empty table is
  indistinguishable from perfect isolation. Confirm the preconditions of a
  test before believing its result.
- **Prefer the failure that is loud.** A stale selector returning zero
  tenders is indistinguishable from a quiet week in procurement, which is why
  portal contracts carry a `verified` flag and refuse to run when it is off.
- **Return `null`, never a guess.** A null flows as "not stated"; a guess
  flows as fact. This is load-bearing in Hebrew date parsing, where
  `03/04/2026` read as US format is a deadline a month early and still a
  valid date.
- **Done means:** `npm run typecheck` passes in `web/`, `pytest -q` passes in
  `scraper-service/`, new SQL is a new numbered migration with its RLS policy
  in the same file, and `REPOMIX.md` records any architectural change.

## Your first move

Do not start coding. Do this instead:

1. Read `CLAUDE.md` and `REPOMIX.md`.
2. Run `git pull --rebase origin main` and report the HEAD you land on — if
   it is not `19b9182`, the other session has pushed and you should read
   what changed.
3. Confirm the checks pass locally as they stand: `pytest -q` in
   `scraper-service/`, and `npm run typecheck` in `web/`.
4. Tell the user, in Hebrew and briefly, what you found and what you propose
   as the first task — and if the Supabase MCP server is configured, confirm
   it answers by listing the tables.

Then work item 1 and 2 from the queue above: get an account and organization
in place, then get real tenders into the database.
