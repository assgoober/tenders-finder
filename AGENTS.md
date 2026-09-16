# AGENTS.md — Tenders Finder Agent Contract

This repository holds one project. Everything in it is Tenders Finder.

| Path | Piece | Stack |
|---|---|---|
| `web/` | Dashboard | Next.js 14 |
| `scraper-service/` | Aggregation agents + NLP extraction | Python · FastAPI · Playwright |
| `supabase/` | Schema, RLS, matching engine | PostgreSQL + pgvector |
| `landing/` | Public landing page | Hebrew source, English generated (`npm run build:en`) |

Project knowledge base: `REPOMIX.md` at the repository root. Read it before
making architectural decisions.

## Product

Tenders Finder solves the extreme fragmentation of defense procurement
tenders, which are scattered across hundreds of isolated government and
military portals. Business-development teams currently search them manually
and lose opportunities.

Three pillars:

1. **Automated aggregation agents** — harvest tenders from many portals.
2. **AI alert engine** — semantic matching of new tenders to a company profile.
3. **Unified workspace** — one dashboard replacing dozens of portal logins.

Optimize every UI decision for reduced cognitive load. Optimize every data
decision for trust: always show the extracted field next to a link to the
original source document.

## Brand and visual identity

`landing/index.html` is the single source of truth for the brand. It carries
the logo, the full colour token set for both themes, and a design plan in a
comment at the top of the file explaining why each choice was made.

It is also the Hebrew source of truth for the site's copy. `landing/en/`
is **generated** from it by `npm run build:en` against
`landing/content/en.json` — never hand-edited, and never the file you make a
content change in. `npm run check:en` fails when Hebrew has been edited and
its translation has not.

Read it before producing anything the brand appears on -- a deck, a diagram,
a report cover, a second page. Do not invent a palette or a typeface pairing;
a venture that looks like two companies across two documents reads as one that
has not decided what it is.

The load-bearing parts:

- **Logo:** `brand/logo.png` is the master, keyed from the operator's own
  artwork; `landing/logo.png` is that file resized for the web, and the
  favicon and the Apple touch icon are cut from it. Every mark on the site
  comes from one image on purpose -- a favicon that drifts from the header
  mark is a favicon nobody associates with the product. Its aspect ratio is
  **1.567:1**; give it a height and let the width follow. Do not redraw it as
  SVG. A hand-traced approximation was carried here for a while and was wrong
  in both shape and proportion, which nobody caught until it was put beside
  the real thing.
- **Colour:** navy `#12294D`, blue `#1668D8` in the mark and `#2C7BEE` lifted
  for AA on dark, steel, and a blue-biased near-black ground `#05070C` that is
  the logo's own ground rather than a generic dark.
- **Type:** Heebo 800/900 for display, Assistant for body, IBM Plex Mono for
  identifiers. The mono is not decoration: reference numbers and standard
  codes are matched character for character downstream.
- **Semantic colour is separate from the accent.** `live` and `urgent` exist
  so that status never competes with brand. Do not promote a status colour to
  an accent, and do not tint the brand blue to signal state.

Dark is the intended expression. A surface that must work in one theme only
should be the dark one.

## Architecture rules

### Dual-use (B2G today, B2B later)

Model tenders content-agnostically. Put domain-specific extracted fields in
the `metadata` JSONB column, never in dedicated defense-only SQL columns.
A civilian infrastructure or medical-procurement tender must flow through the
same tables, the same extraction pipeline, and the same matching function
without a schema migration.

Concretely: `tenders` carries only universal columns (`title`, `source_url`,
`publication_date`, `deadline`, `status`, `buyer_name`, `metadata`,
`embedding`). Anything sector-specific — security clearance level, ITAR
classification, medical device class — lives inside `metadata`.

### Multi-tenancy and isolation

Every tenant-scoped table carries `organization_id` and has Row Level
Security **enabled and forced**. Defense contractors compete with each other:
one organization must never observe another's alert profiles, pipeline
statuses, or notes. Write an RLS policy in the same migration that creates
the table — never in a follow-up.

Service-role code (the scraper service) writes shared tender records; it must
not write tenant-scoped rows on behalf of a tenant without an explicit
`organization_id`.

### LLM output is never trusted

The LLM's job is semantic understanding and extraction, not judgment. It never
decides whether to bid, who wins, or what a requirement "really means".

Validate every LLM response against a Pydantic model (Python) or Zod schema
(TypeScript) before it reaches the database. Use structured outputs
(`client.messages.parse(..., output_format=Model)`) rather than asking for
JSON in prose. On validation failure: retry once with the validation error fed
back, then flag the tender `needs_review` for a human. Never persist an
unvalidated payload.

Instruct the model to return `null` for fields it cannot confidently extract.
A `null` is a correct answer; a guess is a defect.

A refusal is not an exception. It arrives as a successful HTTP 200 with
`stop_reason: "refusal"` and empty content, so branch on `stop_reason` — never
on the presence of `stop_details`, which is absent on some refusals.

### A tender is matchable before it is read

A tender enters the matching corpus embedded from its **listing** — the
title, description and CPV codes the contracting authority published — and
the full PEGS extraction is deferred until the tender matches somebody's
profile (ADR-22). This is not a weakening of the rule above; it is the
same rule. A listing is the buyer's own words, so at ingestion there is no
model output to validate, because there is no model.

Two consequences that are easy to get wrong:

- **`extraction_status = 'pending'` with an embedding is a healthy row**,
  not an unfinished one. Provenance lives in
  `metadata.embedding.source = 'listing'`. Do not "fix" such rows by
  extracting them on sight; `tenders_awaiting_extraction()` decides which
  ones have earned the cost.
- **A row must never lose its embedding.** An unembedded tender is present
  in every count and absent from every result, which is the worst state
  available — worse than a missing row, which at least announces itself.
  The trigger in 0019 refuses any write that replaces a vector with null,
  whatever the statuses say.

### Extraction prompt structure — PEGS

Every extraction prompt must organize the tender document along PEGS:

- **P**articipation — eligibility, mandatory prerequisites, certifications.
- **E**valuation — how bids are scored.
- **G**uidelines — technical specification, BOM, quantities, delivery terms.
- **S**election — award criteria and decisive metrics.

PEGS gives the model contextual cues that a generic "extract the
requirements" prompt does not, and it measurably improves extraction quality.

### Scraper agents

Separate navigation logic from extraction logic so a portal redesign breaks
only one of them. Register every source through the agent registry
(`app/agents/registry.py`) — adding a portal must never require editing core
pipeline code. Use exponential backoff with jitter on every outbound request;
government portals rate-limit and IP-ban aggressively.

Before enabling a portal in production, check its terms of use and
`robots.txt`. `sapakim.mod.gov.il` serves a genuine blanket disallow and is
off limits. The gov.il JSON API is gated by an `x-client-id` the front end
embeds — do not lift it; request one from the operator.

## Agent tooling in this repository

Two tools, two filenames, one contract. Codex reads this file; Claude Code
reads `CLAUDE.md`, which is a pointer to it. Edit the contract here.

`.codex/config.toml` is committed on purpose. It pins the Supabase MCP
server to `read_only=true` and to this one `project_ref`, and both flags are
load-bearing rather than defaults worth tidying away:

- **`read_only`** — this database fills with text scraped from government
  portals: content nobody on this project wrote, which an agent then reads
  back. That is exactly the path by which instructions injected into data
  become a problem. The cost is real and is meant to be worked with: you
  cannot write to the database. Migrations and data fixes go through the SQL
  Editor, where a human sees the statement before it runs.
- **`project_ref`** — the credential behind that server is scoped to the
  whole Supabase account. Without the pin, an agent reaches every project
  the account has, now and in future.

Do not remove either flag to make a task easier. If a task needs a write,
the answer is SQL handed to the operator.

## Commands

```bash
# Web
cd web && npm install && npm run dev
cd web && npm run typecheck && npm run lint

# Scraper / NLP service
cd scraper-service && pip install -r requirements.txt
cd scraper-service && uvicorn app.main:app --reload
cd scraper-service && pytest -q

# Landing page
cd landing && npm run serve
```

## Done criteria

A task is not complete until:

1. `npm run typecheck` passes in `web` (for web changes).
2. `pytest -q` passes in `scraper-service` (for service changes).
3. New SQL lives in a new numbered file under `supabase/migrations/` — never
   edit an applied migration.
4. Any new tenant-scoped table has an RLS policy in the same migration.
5. `REPOMIX.md` is updated when an architectural decision changes.

Do not commit code that has not passed the checks above.

## Model usage

**Two providers, and which one is live is a setting, not a constant.**
`EXTRACTION_PROVIDER` selects `anthropic` or `gemini`; `extraction_model`
resolves to `ANTHROPIC_MODEL` or `GEMINI_MODEL` accordingly. The running
deployment uses **Gemini** for both extraction and embeddings. Never
hardcode a model id at a call site, and never assume a provider from this
file — read the setting. A version of this section said "use
`claude-opus-5`" for a week after the service had switched, and `/health`
announced a model the service was not running.

**Embeddings are not interchangeable the way extraction models are.**
Extraction output is validated against a schema, so a different model
produces output that either passes or does not. An embedding is a position
in a space that only that model defines: vectors from two models are not
comparable, and cosine similarity between them is noise wearing a score.
Changing `EMBEDDING_PROVIDER` therefore means **re-embedding the entire
corpus**, and is a migration, not a config change. `_assert_dimensions` is
not the guard for this — a wrong-model vector has the right shape.

**The embedding budget is the real ceiling on ingestion.** Gemini's free
tier allows 1,000 embedding *requests* per day and 30,000 *tokens* per
minute. Tokens bind first: a listing averages ~198 tokens, so throughput is
about 150 tenders a minute regardless of batch size. Batching buys request
headroom, not speed. Anything that embeds in a loop, one item per call, will
spend a day's budget on a thousand rows — that has happened here twice.
