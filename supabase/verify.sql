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
  'tables'   as check, count(*)::text as got, '10' as expect,
  case when count(*) = 10 then 'OK' else 'CHECK' end as status
from pg_class c join pg_namespace n on n.oid=c.relnamespace
where n.nspname='public' and c.relkind='r'
union all
select 'RLS enabled on all', count(*)::text, '10',
  case when count(*) = 10 then 'OK' else 'CHECK' end
from pg_class c join pg_namespace n on n.oid=c.relnamespace
where n.nspname='public' and c.relkind='r' and c.relrowsecurity
union all
select 'RLS forced (tenant tables)', count(*)::text, '5',
  case when count(*) = 5 then 'OK' else 'CHECK' end
from pg_class c join pg_namespace n on n.oid=c.relnamespace
where n.nspname='public' and c.relkind='r' and c.relforcerowsecurity
union all
select 'policies', count(*)::text, '12',
  case when count(*) = 12 then 'OK' else 'CHECK' end
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
-- Everything above counts catalogue rows. Counting is blind to the bug that
-- migration 0007 shipped: a `revoke ... from anon` that removed anon's name
-- from a function's ACL while the default grant to PUBLIC, which anon
-- inherits, left it executable anyway. The ACL read as closed; the privilege
-- was open. Only an effective-privilege question separates the two, so the
-- checks below ask that question instead of counting.
select 'anon cannot execute generate_matches', has_function_privilege('anon','public.generate_matches_for_tender(uuid)','EXECUTE')::text, 'false',
  case when has_function_privilege('anon','public.generate_matches_for_tender(uuid)','EXECUTE')
       then 'CHECK' else 'OK' end
union all
-- The other half of that fix: revoking too widely would have taken the RLS
-- policies down with it, since all six tenant policies call these two.
select 'RLS helpers still callable', (has_function_privilege('authenticated','public.auth_org_ids()','EXECUTE')
  and has_function_privilege('authenticated','public.is_org_member(uuid)','EXECUTE'))::text, 'true',
  case when has_function_privilege('authenticated','public.auth_org_ids()','EXECUTE')
        and has_function_privilege('authenticated','public.is_org_member(uuid)','EXECUTE')
       then 'OK' else 'CHECK' end
union all
-- leads (0006) is the only table a signed-out visitor writes to. Write-only
-- is the whole control, so both halves are asserted.
select 'leads: anon may insert', has_table_privilege('anon','public.leads','INSERT')::text, 'true',
  case when has_table_privilege('anon','public.leads','INSERT') then 'OK' else 'CHECK' end
union all
select 'leads: anon may not read', has_table_privilege('anon','public.leads','SELECT')::text, 'false',
  case when has_table_privilege('anon','public.leads','SELECT') then 'CHECK' else 'OK' end
union all
select 'pgvector extension', count(*)::text, '1',
  case when count(*) = 1 then 'OK' else 'CHECK' end
from pg_extension where extname='vector';
