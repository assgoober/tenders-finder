-- 0006_leads.sql
-- Inbound contact submissions from the public landing page.
--
-- This is the first table in the schema that an *unauthenticated* caller
-- writes to. The landing page is static and ships the anon key in its own
-- source, so "who may insert" cannot be enforced by keeping a key secret —
-- it has to be enforced here.
--
-- The shape of the protection is therefore: anon may INSERT and nothing
-- else. There is deliberately no SELECT policy for any browser-facing role,
-- so a submission cannot be read back by the page that created it, and one
-- visitor cannot enumerate another's contact details. Only the service role
-- (which bypasses RLS) reads this table. That is the same posture as
-- `scrape_runs` in 0003 — RLS enabled, no policy, service role only.
--
-- The table is NOT tenant-scoped: a lead arrives before any organization
-- exists for it, so it carries no `organization_id` and is not part of the
-- cross-organization isolation surface.

create table if not exists leads (
  id          uuid primary key default gen_random_uuid(),

  -- The six fields the landing form collects. `name`, `company` and `email`
  -- are required there; `phone`, `sector` and `notes` are optional and
  -- arrive as empty strings, so they are nullable here rather than
  -- defaulted — an absent phone is "not stated", not "".
  full_name   text not null,
  company     text not null,
  email       text not null,
  phone       text,
  sector      text,
  notes       text,

  -- Operational, set by whoever works the queue. Not written by the page.
  status      text not null default 'new',

  created_at  timestamptz not null default now(),

  -- Bounds, not validation. A public INSERT endpoint takes whatever the
  -- network offers it, so the length caps exist to stop a payload bomb from
  -- becoming a storage bill. They are generous enough that no honest
  -- submission hits them.
  constraint leads_full_name_len check (char_length(full_name) between 1 and 200),
  constraint leads_company_len   check (char_length(company)   between 1 and 200),
  constraint leads_email_len     check (char_length(email)     between 3 and 320),
  constraint leads_phone_len     check (phone  is null or char_length(phone)  <= 40),
  constraint leads_sector_len    check (sector is null or char_length(sector) <= 80),
  constraint leads_notes_len     check (notes  is null or char_length(notes)  <= 4000),

  -- Deliberately shallow: enough to reject an obvious non-address, not
  -- enough to reject a valid one this regex has not heard of. A wrong
  -- rejection here loses a real lead permanently; a wrong acceptance costs
  -- one row someone skims past.
  constraint leads_email_shape   check (email ~ '^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]{2,}$'),

  constraint leads_status_known  check (status in ('new','contacted','qualified','archived'))
);

create index if not exists leads_created_idx on leads (created_at desc);
create index if not exists leads_status_created_idx on leads (status, created_at desc)
  where status = 'new';

comment on table leads is
  'Landing-page contact submissions. Anon INSERT only; readable by the '
  'service role. Contains personal data — the public page promises access, '
  'correction and deletion on request, so deletions here are a product '
  'obligation, not a cleanup.';

-- ---------------------------------------------------------------------------
-- Privileges
--
-- Supabase grants the browser-facing roles broad default privileges on new
-- tables in `public`. Those defaults are revoked here and re-granted
-- narrowly, so the policy below is not the only thing standing between an
-- anonymous caller and this table's contents.
-- ---------------------------------------------------------------------------

revoke all on table leads from anon, authenticated;
grant insert on table leads to anon;

alter table leads enable row level security;

-- Insert only. No `using` clause, because there is no read to qualify.
drop policy if exists leads_public_insert on leads;
create policy leads_public_insert on leads
  for insert to anon with check (true);

-- No SELECT, UPDATE or DELETE policy exists for `anon` or `authenticated`,
-- and that absence is the control. Do not add one to "let the dashboard show
-- leads" without deciding which signed-in users may see personal data that
-- was submitted to the company, not to a tenant.
