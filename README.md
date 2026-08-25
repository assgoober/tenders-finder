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

Three candidate Israeli sources were considered. Probe results as of
2026-08-25:

| Portal | Agent | Status |
|---|---|---|
| `online.mod.gov.il/…/BalamList.aspx` — MOD tender list | `mod-balam` | **robots.txt allows.** ASP.NET WebForms confirmed. Selectors and column map confirmed. Two blockers remain — see below |
| `sapakim.mod.gov.il/home` — MOD suppliers portal | none, and none planned | **`robots.txt` disallows, for `*` and for our agent. Do not scrape it.** |
| `gov.il` publications collector | `govil` | Not yet probed |

### sapakim.mod.gov.il is off limits

Its `robots.txt` disallows the path for every user agent. That settles it:
no agent, no probe beyond the robots check, no workaround. If this portal's
data is wanted, the route is an access agreement with the operator, not a
scraper.

### mod-balam — confirmed, and still disabled

The probe confirmed `robots.txt` permission, the WebForms architecture,
`table[id*='gv'] tr` as the row selector, and the column map (now recorded
in `BalamContract`). Two findings from that map were load-bearing:

- **The deadline is written time-first** — `16:00 26/08/2026`. The parser
  read only the text after the date, so the submission time was dropped and
  every deadline became midnight: sixteen hours early, and a perfectly valid
  timestamp that nothing downstream would question. Fixed, with a test.
- **Titles carry a `המשך...` truncation marker.** Left in it would be
  stored, displayed, and embedded — a token every clipped tender shares,
  pulling their vectors together. Stripped, with a test.

Two blockers keep `verified=False`, both invisible at runtime if ignored:

1. **No pagination control was found** — every pager selector matched zero
   elements. If the listing does page and the agent does not follow it, only
   the first page is ever ingested and later tenders are simply never seen.
2. **Row links are `javascript:__doPostBack(...)`, not URLs.** Every tender
   would be skipped, and a tender with no link back to its notice cannot be
   verified by a bid team — which is the product's whole trust model.

The probe now dumps every `__doPostBack` target and argument, which answers
both: an argument like `Page$2` is the pager, anything else is a row link.
Re-run it and both blockers close.

The `verified` guard exists because the failure it prevents is invisible: a
stale selector returns zero tenders, which looks exactly like a quiet week
in procurement. Nothing downstream can tell the difference.

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

python tools/probe_portal.py --url "https://sapakim.mod.gov.il/home"
python tools/probe_portal.py --url "https://www.online.mod.gov.il/Online2016/Pages/General/Balam/BalamList.aspx?Reset=1" --detail-sample
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

python tools\probe_portal.py --url "https://sapakim.mod.gov.il/home"
python tools\probe_portal.py --url "https://www.online.mod.gov.il/Online2016/Pages/General/Balam/BalamList.aspx?Reset=1" --detail-sample
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
