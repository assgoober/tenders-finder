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
| Aggregation agents | Base classes, registry, generic Playwright agent, offline fixture agent. **No real portal agent yet.** |
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
