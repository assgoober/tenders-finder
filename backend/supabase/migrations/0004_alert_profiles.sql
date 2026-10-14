-- 0004_alert_profiles.sql
-- Tenant-private tables: what an organization is looking for, what it was
-- matched to, and where each opportunity stands in its own pipeline.
--
-- Everything in this file is competitively sensitive. All three tables get
-- ENABLE + FORCE row level security scoped through auth_org_ids().

create table if not exists alert_profiles (
  id               uuid primary key default gen_random_uuid(),
  organization_id  uuid not null references organizations(id) on delete cascade,
  name             text not null,

  -- Natural-language description of what the company builds and wants to
  -- bid on. This is the text that gets embedded — the semantic half of the
  -- match. Written by the BD team, not by an admin filling in checkboxes.
  description      text not null,
  embedding        vector(1024),

  -- Deterministic half of the hybrid match. Vector similarity finds the
  -- semantically close tenders; these narrow them to the ones the company
  -- can actually bid on.
  keywords         text[] not null default '{}',
  excluded_keywords text[] not null default '{}',
  min_score        real not null default 0.72,
  filters          jsonb not null default '{}'::jsonb,

  is_active        boolean not null default true,
  notify_email     boolean not null default true,
  created_by       uuid references auth.users(id) on delete set null,
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now(),

  constraint alert_profiles_min_score_range check (min_score >= 0 and min_score <= 1)
);

create index if not exists alert_profiles_org_idx
  on alert_profiles (organization_id) where is_active;

create index if not exists alert_profiles_embedding_hnsw_idx on alert_profiles
  using hnsw (embedding vector_cosine_ops);

-- One row per (profile, tender) the matcher accepted. Also the alert ledger:
-- alerted_at null means "matched but not yet delivered".
create table if not exists tender_matches (
  id               uuid primary key default gen_random_uuid(),
  organization_id  uuid not null references organizations(id) on delete cascade,
  profile_id       uuid not null references alert_profiles(id) on delete cascade,
  tender_id        uuid not null references tenders(id) on delete cascade,
  score            real not null,
  -- Why this matched: which keywords hit, the raw vector distance. Shown in
  -- the UI so a BD lead can judge the match instead of trusting it blindly.
  match_reason     jsonb not null default '{}'::jsonb,
  alerted_at       timestamptz,
  dismissed_at     timestamptz,
  created_at       timestamptz not null default now(),

  constraint tender_matches_unique unique (profile_id, tender_id)
);

create index if not exists tender_matches_org_created_idx
  on tender_matches (organization_id, created_at desc);

create index if not exists tender_matches_pending_alert_idx
  on tender_matches (created_at) where alerted_at is null and dismissed_at is null;

-- The organization's own bid lifecycle for a tender. Separate from
-- tenders.status: the tender is 'open' while this org is already 'submitted'
-- and a competitor has not started.
create table if not exists tender_pipeline (
  id               uuid primary key default gen_random_uuid(),
  organization_id  uuid not null references organizations(id) on delete cascade,
  tender_id        uuid not null references tenders(id) on delete cascade,
  status           pipeline_status not null default 'new',
  owner_user_id    uuid references auth.users(id) on delete set null,
  notes            text,
  -- Manual ordering inside a Kanban column.
  position         integer not null default 0,
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now(),

  constraint tender_pipeline_unique unique (organization_id, tender_id)
);

create index if not exists tender_pipeline_board_idx
  on tender_pipeline (organization_id, status, position);

create or replace trigger alert_profiles_set_updated_at
  before update on alert_profiles
  for each row execute function set_updated_at();

create or replace trigger tender_pipeline_set_updated_at
  before update on tender_pipeline
  for each row execute function set_updated_at();

-- ---------------------------------------------------------------------------
-- Row Level Security
-- ---------------------------------------------------------------------------

alter table alert_profiles  enable row level security;
alter table alert_profiles  force  row level security;
alter table tender_matches  enable row level security;
alter table tender_matches  force  row level security;
alter table tender_pipeline enable row level security;
alter table tender_pipeline force  row level security;

drop policy if exists alert_profiles_rw on alert_profiles;
create policy alert_profiles_rw on alert_profiles
  for all to authenticated
  using (organization_id in (select auth_org_ids()))
  with check (organization_id in (select auth_org_ids()));

-- Matches are produced by the service role; members read and dismiss them.
drop policy if exists tender_matches_select on tender_matches;
create policy tender_matches_select on tender_matches
  for select to authenticated
  using (organization_id in (select auth_org_ids()));

drop policy if exists tender_matches_update on tender_matches;
create policy tender_matches_update on tender_matches
  for update to authenticated
  using (organization_id in (select auth_org_ids()))
  with check (organization_id in (select auth_org_ids()));

drop policy if exists tender_pipeline_rw on tender_pipeline;
create policy tender_pipeline_rw on tender_pipeline
  for all to authenticated
  using (organization_id in (select auth_org_ids()))
  with check (organization_id in (select auth_org_ids()));
