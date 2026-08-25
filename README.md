# Tenders Finder

Aggregates public procurement tenders scattered across isolated government
portals into one workspace: automated scraping agents, schema-constrained AI
extraction, semantic matching against a company profile, and a dashboard that
replaces portal-by-portal searching.

Built B2G-first (defense procurement) on a content-agnostic data model, so the
same pipeline handles civilian and commercial tenders without a schema change.

Full architecture, decision records, and rationale: [`../REPOMIX.md`](../REPOMIX.md).
Agent working rules: [`../CLAUDE.md`](../CLAUDE.md).

## Layout

```
supabase/migrations/   PostgreSQL schema: pgvector, RLS, hybrid matching engine
supabase/seed.sql      Local development data (never run in production)
scraper-service/       Python · FastAPI · Playwright · Claude extraction
web/                   Next.js 14 dashboard
```

## Status

| Piece | State |
|---|---|
| Database schema, RLS, matching functions | Written; **not yet applied to a live Postgres** |
| Aggregation agents | `mod-balam` **verified against the live portal and enabled**; `govil` written but unprobed; plus base classes, registry and an offline fixture agent |
| NLP extraction (PEGS + Pydantic) | Complete, tested against a mocked client |
| Alert engine | SQL functions and ingestion trigger written |
| Dashboard | Complete, running on mock data |

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
cd scraper-service
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium     # only needed for real portal agents

cp ../.env.example ../.env      # then fill it in
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

### 3. Dashboard

```bash
cd web
npm install
npm run dev          # http://localhost:3000
```

The dashboard renders from `lib/mock-data.ts`, so it works before the database
exists. Wiring live data means replacing the imports in `app/page.tsx` with
Supabase queries under the user's session — RLS scopes them to the caller's
organization, so no component needs an organization filter of its own.

## Israeli portal agents

Three candidate Israeli sources were considered. Probe results as of
2026-08-25:

| Portal | Agent | Status |
|---|---|---|
| `online.mod.gov.il/…/BalamList.aspx` — MOD tender list | `mod-balam` | **Verified and enabled.** ASP.NET WebForms; selectors, column map, pager and detail URL all confirmed live |
| `sapakim.mod.gov.il/home` — MOD suppliers portal | none, and none planned | **`robots.txt` disallows, for `*` and for our agent. Do not scrape it.** |
| `gov.il` publications collector | `govil` | **`robots.txt` disallows** both candidate paths, for `*` and for our agent. Stays disabled |

### Two of the three are off limits

`sapakim.mod.gov.il` and `www.gov.il` both disallow their tender paths in
`robots.txt`, for `*` and for our agent alike. That settles both: no agent,
no probe beyond the robots check, no workaround. If their data is wanted,
the route is an access agreement with the operator, not a scraper.

This matters for planning. The assumption that breadth could be added
without negotiating access does not hold for Israeli government portals —
of the three checked, only the MOD BALAM list permits crawling, and it is
the one that publishes no documents. Expanding coverage here means asking
for access, or looking at sources that publish open data deliberately.

On a disallow the probe now also fetches `robots.txt` verbatim and reports
whether the whole host is closed or only specific routes, lists any declared
sitemaps, and flags the case where a server returns an HTML error page at
`/robots.txt` — which parses as "disallow everything" while being no policy
at all. A blanket close and a single closed route call for different next
steps, so the verdict alone is not enough to act on.

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
git clone https://github.com/omer-izhaki/ai-trainer-cal-counter.git
cd ai-trainer-cal-counter
git checkout claude/tenders-finder-architecture-piz4ft
cd tenders-finder/scraper-service

pip install playwright
playwright install chromium

python tools/probe_portal.py --url "https://www.gov.il/he/collectors/publications?publicationType=tenders" --detail-sample
```

**Windows PowerShell** — backslashes, and one command per line
(PowerShell 5.1 does not accept `&&`):

```powershell
git clone https://github.com/omer-izhaki/ai-trainer-cal-counter.git
cd ai-trainer-cal-counter
git checkout claude/tenders-finder-architecture-piz4ft
cd tenders-finder\scraper-service

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

1. Create `scraper-service/app/agents/<portal>.py`.
2. Subclass `PlaywrightPortalAgent` (listing + detail portals) or
   `TenderSourceAgent` (anything else) and decorate it with `@register_agent`.
3. Import it in `app/agents/__init__.py`.

Nothing in the pipeline changes. Keep navigation (`discover`) and extraction
(`fetch`) separate — a portal redesign usually breaks only one of them.

**Before enabling a portal in production, check its terms of service and
`robots.txt`.**

## Checks

```bash
cd scraper-service && pytest -q
cd web && npm run typecheck && npm run lint && npm run build
```
