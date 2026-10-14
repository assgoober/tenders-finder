-- Deliver a match to the person it is for.
--
-- 0004 anticipated this exactly: `tender_matches.alerted_at` is nullable and
-- there is a partial index on `(created_at) where alerted_at is null and
-- dismissed_at is null`, commented "matched but not yet delivered". The
-- column has existed since then and nothing has ever written to it, because
-- nothing has ever delivered anything. Thirty-five matches are sitting in
-- that index right now.
--
-- Two functions, because delivery has exactly two database-shaped questions: who
-- should receive what, and recording that they did.

-- ---------------------------------------------------------------------------
-- pending_match_alerts: every undelivered match, with the addresses to send
-- it to.
--
-- SECURITY DEFINER because recipients live in auth.users, which no tenant
-- role can read. It returns only the addresses of members of the organisation
-- that owns the match, so nothing crosses a tenant boundary -- the same
-- argument generate_matches_for_tender makes, and the reason it is granted
-- to service_role alone.
--
-- Deliberately returns one row per (match, recipient) rather than an
-- aggregate: the caller groups and renders. A database function that formats
-- an email is a database function that has to be redeployed to change a
-- sentence.
-- ---------------------------------------------------------------------------
create or replace function public.pending_match_alerts(p_limit integer default 500)
returns table (
  match_id        uuid,
  organization_id uuid,
  organization    text,
  recipient       text,
  profile_name    text,
  score           real,
  similarity      numeric,
  tender_id       uuid,
  external_id     text,
  title           text,
  buyer_name      text,
  source_url      text,
  deadline        timestamptz,
  tender_status   tender_status
)
language sql
stable
security definer
set search_path = public
as $$
  select
    m.id,
    m.organization_id,
    o.name,
    u.email::text,
    p.name,
    m.score,
    (m.match_reason->>'similarity')::numeric,
    t.id,
    t.external_id,
    t.title,
    t.buyer_name,
    t.source_url,
    t.deadline,
    t.status
  from tender_matches m
  join organizations o        on o.id = m.organization_id
  join alert_profiles p       on p.id = m.profile_id
  join tenders t              on t.id = m.tender_id
  join organization_members om on om.organization_id = m.organization_id
  join auth.users u           on u.id = om.user_id
  where m.alerted_at is null
    and m.dismissed_at is null
    -- A match whose tender has already closed is not an alert, it is an
    -- apology. The lifecycle job keeps `status` honest hourly (0012), so this
    -- filter can trust it.
    and t.status in ('open', 'closing_soon')
    and (t.deadline is null or t.deadline > now())
    and p.is_active
    and p.notify_email
    and u.email is not null
  -- Soonest deadline first: the whole product is that a deadline is coming.
  order by t.deadline asc nulls last, m.score desc
  limit p_limit;
$$;

comment on function public.pending_match_alerts(integer) is
  'Undelivered matches joined to the addresses that should receive them. '
  'Excludes tenders that have already closed. Read-only.';

revoke execute on function public.pending_match_alerts(integer) from public;
revoke execute on function public.pending_match_alerts(integer) from anon, authenticated;
grant  execute on function public.pending_match_alerts(integer) to service_role;


-- ---------------------------------------------------------------------------
-- mark_matches_alerted: record delivery.
--
-- Separate from the read on purpose. The send happens between them and can
-- fail, and a function that read-and-marked in one statement would mark a
-- match delivered that never left the building.
-- ---------------------------------------------------------------------------
create or replace function public.mark_matches_alerted(p_match_ids uuid[])
returns integer
language sql
security definer
set search_path = public
as $$
  with updated as (
    update tender_matches
       set alerted_at = now()
     where id = any(p_match_ids)
       and alerted_at is null
    returning 1
  )
  select count(*)::integer from updated;
$$;

comment on function public.mark_matches_alerted(uuid[]) is
  'Stamp alerted_at on delivered matches. Called after a successful send, '
  'never as part of reading them.';

revoke execute on function public.mark_matches_alerted(uuid[]) from public;
revoke execute on function public.mark_matches_alerted(uuid[]) from anon, authenticated;
grant  execute on function public.mark_matches_alerted(uuid[]) to service_role;


-- What is waiting to go out right now.
select organization, recipient, count(*) as pending,
       min(deadline)::date as soonest
from public.pending_match_alerts(500)
group by organization, recipient
order by pending desc;
