-- 0003_tenders.sql
-- The tender record itself, plus source documents and scrape observability.
--
-- DUAL-USE CONTRACT (REPOMIX.md §1, CLAUDE.md):
-- Columns here are universal to any public procurement notice in any sector.
-- Nothing defense-specific gets its own column. Security clearance level,
-- ITAR classification, medical device class — all of it lives inside
-- `metadata`, which is populated from the PEGS extraction. A municipal
-- infrastructure tender must be storable here without a migration.

create table if not exists tender_sources (
  id            uuid primary key default gen_random_uuid(),
  -- Matches the key registered in scraper-service/app/agents/registry.py.
  key           text not null unique,
  display_name  text not null,
  base_url      text not null,
  country       text,
  -- Free-form; not an enum, because sources arrive faster than migrations.
  sector        text not null default 'defense',
  is_active     boolean not null default true,
  created_at    timestamptz not null default now()
);

create table if not exists tenders (
  id                uuid primary key default gen_random_uuid(),

  -- Identity of the notice at its origin. Together these dedupe re-scrapes.
  source_id         uuid references tender_sources(id) on delete set null,
  external_id       text,
  source_url        text not null,

  -- Universal columns.
  title             text not null,
  summary           text,
  buyer_name        text,
  publication_date  date,
  deadline          timestamptz,
  status            tender_status not null default 'open',
  currency          text,
  estimated_value   numeric(18, 2),

  -- Content-agnostic extraction payload, shaped by the PEGS framework:
  -- { participation: {...}, evaluation: {...}, guidelines: {...},
  --   selection: {...}, sector_specific: {...} }
  metadata          jsonb not null default '{}'::jsonb,

  -- Raw text kept for re-extraction when the prompt or schema evolves;
  -- re-running the pipeline must never require re-scraping the portal.
  raw_text          text,

  embedding         vector(1024),

  extraction_status extraction_status not null default 'pending',
  extraction_error  text,
  extraction_model  text,

  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now(),

  constraint tenders_source_external_unique unique (source_id, external_id)
);

create table if not exists tender_documents (
  id          uuid primary key default gen_random_uuid(),
  tender_id   uuid not null references tenders(id) on delete cascade,
  file_name   text not null,
  file_url    text not null,
  mime_type   text,
  byte_size   bigint,
  -- Extracted plain text of this attachment, fed to the NLP layer.
  text_content text,
  created_at  timestamptz not null default now()
);

create index if not exists tender_documents_tender_idx
  on tender_documents (tender_id);

-- Observability for the aggregation layer: which agent ran, what it found,
-- and how it failed. Without this, a silently broken portal agent looks
-- identical to a quiet week in procurement.
create table if not exists scrape_runs (
  id             uuid primary key default gen_random_uuid(),
  source_id      uuid references tender_sources(id) on delete set null,
  started_at     timestamptz not null default now(),
  finished_at    timestamptz,
  items_found    integer not null default 0,
  items_ingested integer not null default 0,
  ok             boolean,
  error          text
);

create index if not exists scrape_runs_source_started_idx
  on scrape_runs (source_id, started_at desc);

-- Filter indexes for the hybrid search path in 0005.
create index if not exists tenders_deadline_idx    on tenders (deadline);
create index if not exists tenders_status_idx      on tenders (status);
create index if not exists tenders_published_idx   on tenders (publication_date desc);
create index if not exists tenders_metadata_gin    on tenders using gin (metadata jsonb_path_ops);

-- Keyword half of hybrid search. Kept alongside the vector index rather than
-- replaced by it: exact part numbers, standard codes (MIL-STD-810), and
-- tender reference numbers are precisely what embeddings blur.
create index if not exists tenders_fts_idx on tenders
  using gin (to_tsvector('simple', coalesce(title, '') || ' ' || coalesce(summary, '')));

-- HNSW over cosine distance. Built for read-heavy matching: new tenders
-- arrive in the tens per day, queries run per organization profile.
create index if not exists tenders_embedding_hnsw_idx on tenders
  using hnsw (embedding vector_cosine_ops)
  with (m = 16, ef_construction = 64);

create or replace trigger tenders_set_updated_at
  before update on tenders
  for each row execute function set_updated_at();

-- A published government tender is public information; the competitive
-- secret is which organization is *interested* in it (see 0004). So tenders
-- are readable by any authenticated user, and writable only by the service
-- role that runs the scraper pipeline.
alter table tenders          enable row level security;
alter table tender_documents enable row level security;
alter table tender_sources   enable row level security;
alter table scrape_runs      enable row level security;

drop policy if exists tenders_read on tenders;
create policy tenders_read on tenders
  for select to authenticated using (true);

drop policy if exists tender_documents_read on tender_documents;
create policy tender_documents_read on tender_documents
  for select to authenticated using (true);

drop policy if exists tender_sources_read on tender_sources;
create policy tender_sources_read on tender_sources
  for select to authenticated using (true);

-- scrape_runs is operational telemetry: no policy for `authenticated`, so
-- only the service role (which bypasses RLS) can read or write it.
