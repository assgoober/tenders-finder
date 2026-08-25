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
| Aggregation agents | Base classes, registry, generic Playwright agent, offline fixture agent, and the gov.il agent — **its selectors are unverified, see below** |
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

Three candidate Israeli sources, and they are three different architectures.
Which one you are facing decides the whole agent design:

| Portal | Agent | Architecture |
|---|---|---|
| `online.mod.gov.il/…/BalamList.aspx` — MOD tender list | `mod-balam` | ASP.NET WebForms, postback pagination |
| `sapakim.mod.gov.il/home` — MOD suppliers portal | *not built* | SPA — almost certainly a JSON API behind it |
| `gov.il` publications collector | `govil` | server-rendered or XHR |

**Both agents ship disabled.** Their contract blocks have `verified=False`
and they raise `PortalContractUnverified` rather than running. The
normalization, pagination and dedup logic is written and tested; the
selectors are not — nothing was observed against a live site, because the
environment this was built in has no network route to any of these hosts.

The guard exists because the failure it prevents is invisible: a stale
selector returns zero tenders, which looks exactly like a quiet week in
procurement. Nothing downstream can tell the difference.

### Verifying a portal

From a machine that can reach it:

```bash
cd tenders-finder/scraper-service
python -m tools.probe_portal --url "https://sapakim.mod.gov.il/home"
python -m tools.probe_portal --url "https://www.online.mod.gov.il/Online2016/Pages/General/Balam/BalamList.aspx?Reset=1" --detail-sample
```

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
