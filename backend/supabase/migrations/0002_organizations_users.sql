-- 0002_organizations_users.sql
-- Tenancy: organizations, membership, and the RLS foundation everything else
-- builds on.
--
-- Defense contractors compete with each other. Isolation is a product
-- requirement, not a hardening step: RLS is ENABLED and FORCED on every
-- tenant-scoped table, and the policy ships in the same migration as the
-- table (CLAUDE.md, "Multi-tenancy and isolation").

create table if not exists organizations (
  id           uuid primary key default gen_random_uuid(),
  name         text not null,
  slug         text not null unique,
  -- Sector is a label, never a schema branch. 'defense' today, 'municipal' or
  -- 'healthcare' later, with no migration (REPOMIX.md ADR-1 dual-use).
  sector       text not null default 'defense',
  settings     jsonb not null default '{}'::jsonb,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);

create table if not exists organization_members (
  organization_id uuid not null references organizations(id) on delete cascade,
  user_id         uuid not null references auth.users(id) on delete cascade,
  role            org_role not null default 'member',
  created_at      timestamptz not null default now(),
  primary key (organization_id, user_id)
);

create index if not exists organization_members_user_idx
  on organization_members (user_id);

-- Central authority for every RLS policy in the schema. Defined SECURITY
-- DEFINER so policies can consult membership without recursing into
-- organization_members' own policies.
create or replace function auth_org_ids()
returns setof uuid
language sql
stable
security definer
set search_path = public
as $$
  select organization_id
  from organization_members
  where user_id = auth.uid();
$$;

create or replace function is_org_member(target_org uuid)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
    select 1
    from organization_members
    where user_id = auth.uid()
      and organization_id = target_org
  );
$$;

alter table organizations          enable row level security;
alter table organizations          force  row level security;
alter table organization_members   enable row level security;
alter table organization_members   force  row level security;

-- A member sees only their own organizations.
drop policy if exists organizations_select on organizations;
create policy organizations_select on organizations
  for select to authenticated
  using (is_org_member(id));

drop policy if exists organizations_update on organizations;
create policy organizations_update on organizations
  for update to authenticated
  using (
    exists (
      select 1 from organization_members m
      where m.organization_id = organizations.id
        and m.user_id = auth.uid()
        and m.role in ('owner', 'admin')
    )
  );

-- A member sees the roster of their own organizations, and nothing else.
-- Critically: never the roster of a competitor.
drop policy if exists organization_members_select on organization_members;
create policy organization_members_select on organization_members
  for select to authenticated
  using (is_org_member(organization_id));

drop policy if exists organization_members_write on organization_members;
create policy organization_members_write on organization_members
  for all to authenticated
  using (
    exists (
      select 1 from organization_members m
      where m.organization_id = organization_members.organization_id
        and m.user_id = auth.uid()
        and m.role in ('owner', 'admin')
    )
  )
  with check (
    exists (
      select 1 from organization_members m
      where m.organization_id = organization_members.organization_id
        and m.user_id = auth.uid()
        and m.role in ('owner', 'admin')
    )
  );

create or replace trigger organizations_set_updated_at
  before update on organizations
  for each row execute function set_updated_at();
