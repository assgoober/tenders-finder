-- Do not send an alert about a tender that closes today.
--
-- The first alert this product was about to send led with a tender closing in
-- 0.3 days. It was a correct match, correctly scored, correctly ordered by
-- urgency -- and useless, because nobody assembles a defence bid in seven
-- hours. An alert that arrives on the closing day is not an alert. It is a
-- receipt.
--
-- Measured on the 35 matches waiting to go out:
--
--   mean lead time      16.0 days
--   under 7 days         6
--   under 3 days         2
--   under 1 day          2   <- both of these would have led the email
--
-- Two thirds of what is waiting is genuinely actionable. The damage is done
-- entirely by the tail, and the tail sorts to the top precisely because the
-- ordering is by urgency.
--
-- The threshold lives on the profile, not in the code, because "enough time"
-- is a property of the bidder rather than of the tender. A spare-parts
-- supplier can answer in three days. A systems integrator assembling
-- consortium letters and clearance paperwork cannot do it in three weeks.
-- Three days is the default only because it is the smallest window in which
-- any of our own matched tenders could plausibly be answered; it is expected
-- to be raised per customer, not treated as a constant.

alter table alert_profiles
  add column if not exists min_lead_days integer not null default 3;

alter table alert_profiles
  drop constraint if exists alert_profiles_min_lead_days_range;
alter table alert_profiles
  add constraint alert_profiles_min_lead_days_range
  check (min_lead_days >= 0 and min_lead_days <= 90);

-- Why a match never went out. A match is still a match -- it stays visible in
-- the dashboard, where a bid team can decide for itself whether seven hours
-- is enough. This column is only about the email.
alter table tender_matches
  add column if not exists alert_skipped_reason text;

create index if not exists tender_matches_pending_idx
  on tender_matches (created_at)
  where alerted_at is null and dismissed_at is null and alert_skipped_reason is null;


-- ---------------------------------------------------------------------------
-- skip_late_matches: retire matches that can no longer be acted on.
--
-- Without this they stay pending forever: the alert query would keep
-- excluding them and nothing would ever resolve them, so the queue would grow
-- a permanent sediment of tenders nobody can bid on. Stamping a reason is
-- what makes "we chose not to send this" different from "we lost it".
-- ---------------------------------------------------------------------------
create or replace function public.skip_late_matches()
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
  v_count integer;
begin
  with late as (
    update tender_matches m
       set alert_skipped_reason = 'insufficient_lead_time'
      from alert_profiles p, tenders t
     where p.id = m.profile_id
       and t.id = m.tender_id
       and m.alerted_at is null
       and m.dismissed_at is null
       and m.alert_skipped_reason is null
       and t.deadline is not null
       and t.deadline < now() + make_interval(days => p.min_lead_days)
    returning 1
  )
  select count(*)::integer into v_count from late;

  return v_count;
end;
$$;

comment on function public.skip_late_matches() is
  'Retire pending matches whose tender closes inside the profile''s minimum '
  'lead time. They stay visible in the dashboard; only the email is skipped.';

revoke execute on function public.skip_late_matches() from public;
revoke execute on function public.skip_late_matches() from anon, authenticated;
grant  execute on function public.skip_late_matches() to service_role;


-- ---------------------------------------------------------------------------
-- pending_match_alerts: honour the lead time, and hand the renderer the
-- number of days so the email can say how tight a tender is rather than
-- leaving the reader to work it out.
--
-- Dropped and recreated rather than replaced. `create or replace` cannot
-- change a function's return type, and adding `lead_days` to the returned
-- row changes it:
--
--   ERROR 42P13: cannot change return type of existing function
--   DETAIL: Row type defined by OUT parameters is different.
--
-- The first attempt at this migration failed on exactly that and rolled the
-- whole file back -- loudly, and with nothing half-applied, which is the
-- opposite of most of the defects recorded in REPOMIX. The drop takes the
-- grants with it, which is why 0016's revokes and grant are restated below
-- rather than assumed to have survived.
-- ---------------------------------------------------------------------------
drop function if exists public.pending_match_alerts(integer);

create function public.pending_match_alerts(p_limit integer default 500)
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
  lead_days       integer,
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
    case when t.deadline is null then null
         else floor(extract(epoch from (t.deadline - now())) / 86400)::integer
    end,
    t.status
  from tender_matches m
  join organizations o        on o.id = m.organization_id
  join alert_profiles p       on p.id = m.profile_id
  join tenders t              on t.id = m.tender_id
  join organization_members om on om.organization_id = m.organization_id
  join auth.users u           on u.id = om.user_id
  where m.alerted_at is null
    and m.dismissed_at is null
    and m.alert_skipped_reason is null
    and t.status in ('open', 'closing_soon')
    and p.is_active
    and p.notify_email
    and u.email is not null
    -- A tender with no published deadline is not late; it is unknown, and a
    -- bid team would rather see it than not.
    and (
      t.deadline is null
      or t.deadline >= now() + make_interval(days => p.min_lead_days)
    )
  order by t.deadline asc nulls last, m.score desc
  limit p_limit;
$$;

comment on function public.pending_match_alerts(integer) is
  'Undelivered matches with enough lead time to act on, joined to the '
  'addresses that should receive them. Read-only.';

revoke execute on function public.pending_match_alerts(integer) from public;
revoke execute on function public.pending_match_alerts(integer) from anon, authenticated;
grant  execute on function public.pending_match_alerts(integer) to service_role;


-- Retire the ones already too late, then show the effect: what goes out, what
-- was skipped, and the soonest deadline that survives. If `soonest` is still
-- today, this migration did not work.
select public.skip_late_matches() as retired_as_too_late;

select
  (select count(*) from public.pending_match_alerts(500))                  as will_send,
  (select count(*) from tender_matches
     where alert_skipped_reason = 'insufficient_lead_time')                as skipped_too_late,
  (select min(deadline)::date from public.pending_match_alerts(500))       as soonest_that_survives,
  (select min(lead_days) from public.pending_match_alerts(500))            as least_lead_days;
