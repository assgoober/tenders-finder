-- 0001_extensions.sql
-- Extensions and shared enums for Tenders Finder.
--
-- EMBEDDING DIMENSION: 1024 (Voyage AI voyage-3-large).
-- This value is duplicated in scraper-service/app/config.py as EMBEDDING_DIM.
-- Changing the embedding provider requires a new migration that ALTERs every
-- vector column AND a full re-embedding of existing rows. See REPOMIX.md ADR-6.

create extension if not exists vector;
create extension if not exists pgcrypto;
create extension if not exists pg_trgm;

-- Lifecycle of the tender itself, as published by the buyer.
-- Deliberately generic: applies to a defense RFP and to a municipal
-- infrastructure tender alike (dual-use architecture, REPOMIX.md §1).
do $$ begin
  create type tender_status as enum ('open', 'closing_soon', 'closed', 'awarded', 'cancelled');
exception when duplicate_object then null;
end $$;

-- Lifecycle of one organization's response to a tender. Distinct from
-- tender_status: two organizations can sit at different stages on the same
-- tender.
do $$ begin
  create type pipeline_status as enum ('new', 'evaluating', 'drafting', 'submitted', 'won', 'lost', 'no_bid');
exception when duplicate_object then null;
end $$;

-- Result of the NLP extraction pass. 'needs_review' is the Human-in-the-Loop
-- escape hatch used when schema validation fails twice (REPOMIX.md ADR-2).
do $$ begin
  create type extraction_status as enum ('pending', 'extracted', 'needs_review', 'failed');
exception when duplicate_object then null;
end $$;

do $$ begin
  create type org_role as enum ('owner', 'admin', 'member', 'viewer');
exception when duplicate_object then null;
end $$;

-- Keeps updated_at honest without application-layer discipline.
create or replace function set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;
