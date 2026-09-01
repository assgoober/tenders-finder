-- Schema verification — run in the Supabase SQL Editor after applying
-- migrations/0001 through 0005.
--
-- "Success. No rows returned" tells you a script parsed and ran. It does not
-- tell you the schema is what the contract requires: a policy that failed to
-- attach, an index that silently did not build, or a trigger that never got
-- created all produce exactly that message. This asserts the shape instead.
--
-- Every row must read OK. A CHECK means the migration that owns that line
-- did not fully take, and re-running it is the fix.
--
-- The expected counts come from an actual run of all five migrations against
-- PostgreSQL 16 with pgvector, where cross-organization isolation was also
-- tested directly: a member of one organization could neither read nor write
-- another's rows, and a signed-in user belonging to no organization saw
-- nothing at all.

select
  'tables'   as check, count(*)::text as got, '9'  as expect,
  case when count(*) = 9  then 'OK' else 'CHECK' end as status
from pg_class c join pg_namespace n on n.oid=c.relnamespace
where n.nspname='public' and c.relkind='r'
union all
select 'RLS enabled on all', count(*)::text, '9',
  case when count(*) = 9 then 'OK' else 'CHECK' end
from pg_class c join pg_namespace n on n.oid=c.relnamespace
where n.nspname='public' and c.relkind='r' and c.relrowsecurity
union all
select 'RLS forced (tenant tables)', count(*)::text, '5',
  case when count(*) = 5 then 'OK' else 'CHECK' end
from pg_class c join pg_namespace n on n.oid=c.relnamespace
where n.nspname='public' and c.relkind='r' and c.relforcerowsecurity
union all
select 'policies', count(*)::text, '11',
  case when count(*) = 11 then 'OK' else 'CHECK' end
from pg_policies where schemaname='public'
union all
select 'project functions', count(*)::text, '7',
  case when count(*) = 7 then 'OK' else 'CHECK' end
from pg_proc p join pg_namespace n on n.oid=p.pronamespace
where n.nspname='public' and p.proname in
  ('auth_org_ids','is_org_member','match_tenders_for_profile','search_tenders',
   'generate_matches_for_tender','on_tender_embedded','set_updated_at')
union all
select 'HNSW vector indexes', count(*)::text, '2',
  case when count(*) = 2 then 'OK' else 'CHECK' end
from pg_indexes where schemaname='public' and indexdef ilike '%hnsw%'
union all
select 'alert trigger on embedding', count(*)::text, '1',
  case when count(*) = 1 then 'OK' else 'CHECK' end
from pg_trigger where tgname='tenders_embedded_match' and not tgisinternal
union all
select 'pgvector extension', count(*)::text, '1',
  case when count(*) = 1 then 'OK' else 'CHECK' end
from pg_extension where extname='vector';
