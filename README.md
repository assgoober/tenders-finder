# Tenders Finder

Aggregates public procurement tenders scattered across isolated government
portals into one workspace: automated scraping agents, schema-constrained AI
extraction, semantic matching against a company profile, and a dashboard that
replaces portal-by-portal searching.

Built B2G-first (defense procurement) on a content-agnostic data model, so the
same pipeline handles civilian and commercial tenders without a schema change.

Full architecture, decision records, and rationale: [`REPOMIX.md`](REPOMIX.md).
Agent working rules: [`AGENTS.md`](AGENTS.md).

## Layout

```
frontend/
  web/                         Next.js 14 dashboard
  landing/                     Public landing page — Hebrew source, English generated
backend/
  scraper-service/             Python · FastAPI · Playwright · LLM extraction (Gemini or Anthropic)
  supabase/migrations/         PostgreSQL schema: pgvector, RLS, hybrid matching engine
  supabase/seed.sql            Local development data (never run in production)
run_sweep.cmd                  Windows launcher for the nightly sweep (stays at the root)
```

## Status

Read back from the live system on 2026-09-15, not asserted.

| | |
|---|---:|
| Tenders | **2,751** — every one carrying an embedding |
| Open for bids | **2,329** |
| Buying countries | **37** |
| Matches | 81 — 66 alerted, 15 skipped as closing too soon, 0 pending |
| Tests | **320**, no network, no API keys |

| Piece | State |
|---|---|
| Database schema, RLS, matching functions | **20 migrations applied and verified against the live project** |
| Aggregation agents | `mod-balam` and `ted-eu` **live**; `uk-fts` written and tested, waiting on embedding budget; `govil` written but disabled on purpose (see below) |
| Extraction (PEGS + Pydantic) | Running on Gemini against real TED notice documents |
| Matching | Semantic and **cross-language** — an English capability profile has matched notices in Greek, Hebrew, Swedish and German |
| Alert engine | **66 alerts delivered**, with a per-profile minimum lead time so nothing arrives too late to bid on |
| Scheduling | 3 hourly `pg_cron` jobs; the harvest runs as a batch job, not a session |
| Dashboard | Reads live data behind authentication |
| Landing page | Live, Hebrew and English at two URLs |

**Not done, and it is the one that matters: there is no customer.** The
system has never run for anyone outside the team that built it.

## Quick start

### 1. Database

```bash
supabase start                 # or point at a hosted project
supabase db reset              # applies migrations/ then seed.sql
```

Migrations are numbered and applied in order. Never edit one that has already
been applied — add a new file.

### 2. Scraper / NLP service

```bash
cd backend/scraper-service
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium     # only needed for real portal agents

cp ../.env.example ../.env      # repo root; then fill it in
uvicorn app.main:app --reload
```

Run the whole pipeline with no API keys and no database:

```bash
EMBEDDING_PROVIDER=hash DRY_RUN=true pytest -q
```

Endpoints:

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Config and registered agents |
| GET | `/sources` | Registered portal agents |
| POST | `/ingest` | Run the pipeline synchronously |
| POST | `/ingest/async` | Kick off a run; poll `scrape_runs` |
| POST | `/extract` | Extract one document without persisting — used for prompt iteration |
| POST | `/profiles/embed` | Backfill embeddings for profiles created in the dashboard |

#### Refusals and fallbacks

A safety classifier can decline a request. That arrives as a **successful
HTTP 200** with `stop_reason: "refusal"` and an empty body — not an
exception — so it has to be checked for explicitly, or it reads as an empty
answer. Tender documents describe munitions, sensors and secure comms, which
is exactly the material adjacent to what the classifiers decline, so false
positives are expected rather than exotic.

`ANTHROPIC_FALLBACKS=true` (the default) re-runs a declined request on a
substitute model server-side, inside the same call. If the whole chain still
declines, the tender goes to `needs_review` with the category recorded in
`metadata.extraction.refusal_category`, and is **not** retried — the same
prompt over the same document draws the same decline.

`/health` reports `refusal_fallbacks`; `/extract` returns `refusal_category`
and `served_by` (which model actually answered — it differs from the
configured model when a fallback rescued the request).

#### Run digest

A `needs_review` tender is deliberately left unembedded, so it raises no
alerts and appears nowhere in the feed. There is no review queue in the
dashboard yet, which means a run that parked a third of its tenders looks
exactly like a run that worked. One digest per run goes to `NOTIFY_EMAIL`
after every source finishes.

It stays silent on a clean run unless `NOTIFY_ON_SUCCESS=true` — a message
that always arrives stops being read. **Without `SMTP_HOST` nothing is
sent**: the digest is logged at WARNING and the run continues, because a
pipeline that aborts over an unreachable mail server has traded the job for
a notice about it.

**Setting it up with Gmail.** Gmail rejects the account password over SMTP;
it needs an App Password, and that option only appears once 2-Step
Verification is on:

1. Google Account → Security → enable **2-Step Verification**.
2. Google Account → Security → **App passwords** → generate one.
3. Put the 16 characters in `SMTP_PASSWORD`, no spaces.

```ini
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=you@gmail.com
SMTP_PASSWORD=<app password>
NOTIFY_EMAIL=you@gmail.com
```

That password goes in `.env` (gitignored) and nowhere else — it grants
access to the account that owns it.

**Prove it before trusting it:**

```bash
python tools/check_notifications.py           # report config, send nothing
python tools/check_notifications.py --send    # deliver a real test digest
```

Exit 0 means delivered, 1 means the config is incomplete, 2 means the server
rejected it — and the tool names the specific setting to change for each
common SMTP failure. Without this the first sign of a misconfiguration is a
run that parks thirty tenders and tells nobody, which is exactly what the
digest exists to prevent.

### 3. Dashboard

```bash
cd frontend/web
npm install
npm run dev          # http://localhost:3000
```

With no credentials it renders `lib/mock-data.ts` and says so in the page, so
a fresh checkout works before the database does.

**Live data needs its own env file.** Next.js reads env from the `frontend/web/`
directory, not the repository root, so the root `.env` the scraper service
uses is invisible to it. Create `frontend/web/.env.local`:

```ini
NEXT_PUBLIC_SUPABASE_URL=https://<ref>.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=<anon / publishable key>
```

Anon key only. The service-role key bypasses RLS entirely, and anything
prefixed `NEXT_PUBLIC_` is compiled into the browser bundle where every
visitor can read it.

These are read **at build time**, not at runtime — that is what `NEXT_PUBLIC_`
means. A build made without them serves demo data regardless of what the
environment holds later, so on Vercel they must be set before the deploy that
is meant to read live data.

Every query runs through the signed-in user's session, so RLS scopes it and no
component carries an organization filter of its own. Three consequences worth
knowing before concluding something is broken:

| What you see | Why |
|---|---|
| Redirected to `/login` | Every policy keys on `auth.uid()`, so signed out reads as an empty database rather than an error. The app refuses to render one |
| Signed in, but no alerts and no pipeline | Your account belongs to no organization. Both are scoped to membership. See [`backend/supabase/README.md`](backend/supabase/README.md) |
| Signed in, no tenders at all | Check `EMBEDDING_PROVIDER`. A tender with no embedding is invisible to every query that matters, and the corpus is empty rather than broken |

Accounts come from `/signup`. A trigger on `auth.users` provisions the
organisation and the owner membership **in the same transaction as the
account** (migration 0017) — the web app cannot do it in a second request,
because a closed tab between the two leaves an account that can sign in and
see nothing, every policy resolving through a membership that does not
exist.

## Sources

Every source is checked before it is connected. A portal whose rules forbid
automated collection is not connected, and the route is an access agreement
with its operator rather than a workaround.

| Portal | Agent | Status |
|---|---|---|
| TED — the EU's official procurement journal | `ted-eu` | **Live.** Anonymous JSON API, 31 CPV divisions, one resumable walk per division. Carries the full notice, so extraction there is genuine rather than title-only |
| `online.mod.gov.il/…/BalamList.aspx` — Israeli MOD tender list | `mod-balam` | **Live.** ASP.NET WebForms; selectors, column map, pager and detail URL confirmed against the live site. Publishes a title and a deadline only, so matching there is shallow by construction |
| `find-tender.service.gov.uk` — UK above-threshold procurement | `uk-fts` | **Written and tested, not yet run against the live budget.** Anonymous OCDS API. Nine notices in ten are awards or plans rather than open tenders, so the filter is most of the value |
| `gov.il` publications collector | `govil` | **JSON API found, gated by an API key.** Needs a client id from the operator. Disabled |
| `sapakim.mod.gov.il/home` — MOD suppliers portal | none, and none planned | **`robots.txt` disallows, for `*` and for our agent. Do not scrape it.** |

### Why the matching is cross-language

The corpus is embedded rather than keyword-indexed, so a capability
description written in English has matched notices published in Greek,
Hebrew, Swedish and German. That is not a design intention being described
— it is what the first real alert contained.

### sapakim.mod.gov.il is off limits

Its `robots.txt` disallows the path for every user agent. That settles it:
no agent, no probe beyond the robots check, no workaround. If this portal's
data is wanted, the route is an access agreement with the operator, not a
scraper.

**Re-confirmed** after the probe learned to tell a real disallow from a
missing `robots.txt`: sapakim serves a genuine `User-agent: * / Disallow: /`.
The verdict was policy, not artefact.

### gov.il is undetermined, not closed

An earlier run here reported "disallowed" and this file recorded gov.il as
closed. **That was wrong.** `www.gov.il/robots.txt` does not exist — the
site is an Angular SPA that serves its homepage for any unmatched path, so
the response is HTML, not a robots policy.

The disallow came from somewhere else: `RobotFileParser` fetches with
urllib's own user-agent, gov.il's CDN answers that with `403`, and RFC 9309
permits a crawler to read a `403` on `robots.txt` as a complete disallow. So
the library reported a prohibition for a site that has stated no policy at
all. Reproduced locally: default library agent gets `403`, ours gets `200`
with the SPA shell.

The probe now separates three outcomes rather than two:

| Verdict | Meaning | Exit |
|---|---|---|
| `allowed` | A real `robots.txt` permits the path | continues |
| `disallowed` | A real `robots.txt` forbids it | `2`, stops |
| `inconclusive` | No `robots.txt` exists to read | `3`, stops unless `--robots-inconclusive-ok` |

`inconclusive` is deliberately neither permission nor prohibition:

- There is no `robots.txt`, so the robots convention imposes no restriction.
- But the operator does filter automated traffic, and the site's terms of
  use govern regardless of what `robots.txt` says or fails to say.

That is a decision for a human, so the probe stops and makes you pass a flag
to continue. It will not let you past a *real* disallow.

### gov.il has a public JSON API

Probing gov.il with `--robots-inconclusive-ok` found it is an Angular SPA
backed by a JSON API on **`openapi-gc.digital.gov.il`** — the government ICT
authority's gateway, with `openapi` in the hostname. That is the integration
target: an API survives redesigns that break every selector, returns
structured data, needs no browser, and is cheap to poll.

The collector exposes a full taxonomy — 149 offices, 85 publication types,
679 topics, 766 units — so tenders can be filtered server-side instead of
fetched and sifted.

The listing call the SPA made returned `{"total": 0, "results": []}`, and the
page showed "אין תוצאות". **That is not an empty portal — it is a wrong
query.** The SPA normalises the URL's `publicationType=tenders` into
`Type=tenders`, and `Type` is a taxonomy filter whose valid values come from
`GetTypesList`; `tenders` is not one of them. The collector type that selects
tender notices is `rfp`.

**The API is gated.** Running `tools/probe_govil_api.py` returned, for every
query:

```json
{"fault":{"faultstring":"Failed to resolve API Key variable request.header.x-client-id",
          "detail":{"errorcode":"steps.oauth.v2.FailedToResolveAPIKey"}}}
```

That is an Apigee gateway demanding an `x-client-id` header. The browser gets
through because the gov.il front end sends its own; a direct call does not.
There is no `robots.txt` on the API host (404), so the robots convention
imposes nothing — **the client id is the control that actually governs access
here.**

The gov.il front end's client id is visible in its page source. **Do not
lift it.** The gateway rejects requests without it, which makes it a
credential rather than a public constant, and using another party's
credential to reach an API is a different act from reading a public page —
it would put this integration on someone else's quota and identity, and
almost certainly breach the terms of use.

The constructive reading: an Apigee gateway exists precisely so that
third parties can be issued their own keys. That is a request to make, and
it is a far better footing than scraping — a sanctioned key means a stable
contract, a quota you can plan against, and no dependency on markup.

Once a client id is issued, the probe takes it directly:

```powershell
python tools\probe_govil_api.py --client-id "<your id>" --out govil-api.json
```

It then checks the API host's robots policy, dumps the tender-related `Type`
values, tries several query shapes and reports `total` for each, and prints
one full result record — the shape the agent maps into a `TenderStub`.

**Note on `www.gov.il`:** there is no `robots.txt` there, and the CDN answers
library user-agents with `403`. That is not a prohibition, but it is a signal
the operator filters automated traffic. The API host is the right door to
knock on, and its policy is what governs.

### mod-balam — what the probe settled

- **`robots.txt` allows it**, for `*` and for our agent, with no crawl-delay
  declared. The conservative defaults in `Settings` stand anyway.
- **Rows:** `table[id*='gv'] tr`, read positionally. Column map in
  `BalamContract`: reference=1, title=2, deadline=4, publication=5,
  contact=6. No buyer column (every row is MOD) and no status column.
- **Pagination:** the pager has no class, id or ARIA label — hence zero
  matches on every container selector — but its links are unambiguous:
  `__doPostBack('ctl00$ContentPlaceHolder1$gvBalam','Page$2')`. Pages 2–9
  were present, so the listing is **at least nine pages deep**; an agent
  reading page one only would have taken about an eighth of the tenders and
  reported success.
- **Detail URLs:** the read-more control is not a postback but a real URL
  behind a dialog opener —
  `ShowDialog('/Online2016/.../BalamHeaderText.aspx?BalamId=1000957926')` —
  confirmed by clicking it and landing there. `BalamId` matched the
  reference number, so a URL can also be built for rows with no read-more
  link. That fallback is load-bearing: the control only renders on titles
  long enough to be clipped, so without it every short-titled tender would
  be dropped.

Two parsing bugs the real data exposed, both of which produce a plausible
wrong value rather than an error:

- **The deadline is written time-first** — `16:00 26/08/2026`. The parser
  read only the text after the date, so the submission time was dropped and
  every deadline became midnight: sixteen hours early, and a valid
  timestamp nothing downstream would question.
- **Titles carry a `המשך...` truncation marker.** Left in it would be
  stored, displayed, and embedded — a token every clipped tender shares.
  It is now stripped, and `enrich` recovers the full title from the detail
  page, which matters because the title is most of what gets embedded and
  the clipped part is exactly the trailing detail that distinguishes
  tenders.

### What this portal actually gives us

The public listing carries reference number, title, deadline, publication
date and a contact phone; `BalamHeaderText.aspx` serves the full title and
nothing else. **There is no public document to run PEGS extraction over** —
specifications and attachments sit behind the supplier login, which this
agent does not touch.

That still leaves aggregation, deadline alerts and semantic matching on the
title, but tenders from this source will have a thin `metadata` payload
compared to a portal that publishes its documents, and the dashboard should
not imply otherwise.

### Verifying a portal

The probe is self-contained and runs from any directory. It needs only
Playwright — not the service's full `requirements.txt`.

**macOS / Linux:**

```bash
git clone https://github.com/omer-izhaki/tenders-finder.git
cd tenders-finder/backend/scraper-service

pip install playwright
playwright install chromium

python tools/probe_portal.py --url "https://www.gov.il/he/collectors/publications?publicationType=tenders" --detail-sample
```

**Windows PowerShell** — backslashes, and one command per line
(PowerShell 5.1 does not accept `&&`):

```powershell
git clone https://github.com/omer-izhaki/tenders-finder.git
cd tenders-finder\backend\scraper-service

pip install playwright
playwright install chromium

python tools\probe_portal.py --url "https://www.gov.il/he/collectors/publications?publicationType=tenders" --detail-sample
```

If a run reports zero items, the URL is probably a landing page rather than
the listing. The probe then dumps the page's tender-related links — probe
whichever of those looks like the real listing. Known candidates for gov.il:

```
/he/collectors/publications?publicationType=tenders
/he/departments/publications?publicationType=tenders
/he/departments/topics/tenders-sub/govil-landing-page
```

Add `--headed` to watch the browser work, and `--out report.json` to save the
full report.

The probe reports whether robots.txt permits the path, classifies the
architecture, lists any JSON endpoints seen over XHR, shows which candidate
selectors match, dumps the GridView column order for WebForms portals, and
saves HTML fixtures under `tests/fixtures/`. Paste the confirmed values into
the portal's contract block and set `verified=True`.

**If the probe finds a JSON endpoint, build against that instead of
selectors.** An API survives redesigns that break every selector, returns
structured data, and is cheaper to poll. That makes `sapakim.mod.gov.il` the
most promising of the three to probe first.

Access may be geo-restricted. If a probe times out or returns a block page
from outside Israel, run it from an Israeli egress.

### WebForms portals

`mod-balam` handles the constraints ASP.NET WebForms imposes, which are not
optional:

- Pagination is `__doPostBack`, so the URL never changes. Pages cannot be
  requested directly, fetched in parallel, or resumed from the middle — the
  agent walks them in order and stops when the row set stops changing.
- `__VIEWSTATE` must round-trip on every postback. That is why the agent
  drives a real browser instead of issuing HTTP requests.
- Filter state lives in the server session, which is what `?Reset=1` is for.
  Every run enters through the reset URL for a deterministic result set.

### Israeli-locale parsing

`app/agents/hebrew.py` handles the things that corrupt data silently rather
than raising:

- **Dates are DD/MM/YYYY.** Read as US MM/DD, `03/04/2026` becomes 4 March
  instead of 3 April — a deadline a month early, and still a valid date.
- **Deadlines are Israel wall-clock time**, and Israel observes DST, so a
  fixed +02:00 offset is an hour wrong for half the year.
- **RTL control characters** are invisible in the browser but present in
  scraped strings, and break exact matching on tender reference numbers.

## Adding a tender portal

1. Create `backend/scraper-service/app/agents/<portal>.py`.
2. Subclass `PlaywrightPortalAgent` (listing + detail portals) or
   `TenderSourceAgent` (anything else) and decorate it with `@register_agent`.
3. Import it in `app/agents/__init__.py`.

Nothing in the pipeline changes. Keep navigation (`discover`) and extraction
(`fetch`) separate — a portal redesign usually breaks only one of them.

**Before enabling a portal in production, check its terms of service and
`robots.txt`.**

## Checks

```bash
cd backend/scraper-service && pytest -q
cd frontend/web && npm run typecheck && npm run lint && npm run build
```
