-- Score a tender against the profile's own distribution, not against a
-- constant.
--
-- `alert_profiles.min_score` has defaulted to 0.72 since 0004. Measured on
-- 2026-09-14 across five profiles and 642 open tenders, it returns nothing at
-- all -- not "too strict", but above the ceiling:
--
--   profile                        best sim   >=0.72  >=0.62  >=0.60  >=0.58
--   Elbit Systems (broad)            0.6510        0       5      21      50
--   Tactical communications (SME)    0.6458        0       2      12      43
--   AIR cargo drone (SME)            0.6414        0       5      12      18
--   Israel Aerospace (broad)         0.6267        0       2      12      39
--   CONTROL office furniture         0.6030        0       0       1      10
--
-- 0.72 was chosen when the plan was Voyage embeddings. The service now runs
-- Gemini truncated to 1024 dimensions, which keeps the ordering and loses the
-- spread: every profile's mean similarity sits at 0.52-0.54 with a standard
-- deviation near 0.027, and office furniture still reaches 0.6030 against a
-- defence corpus. The threshold was never wrong in a way anything could
-- notice, because until today no profile existed to run it.
--
-- Replacing 0.72 with 0.62 would repeat the mistake with a prettier number.
-- The next embedding change would move the distribution again and break it
-- again, silently, in exactly the same way. So the cut is expressed as a
-- position in the profile's own distribution and recomputed from the corpus:
-- "the top one percent for this profile" survives a model swap, and 0.62 for
-- Elbit is not the same event as 0.62 for a cargo-drone SME.
--
-- The absolute floor stays, with a different job. A percentile always returns
-- something -- the top 1% of a corpus containing nothing relevant is still 1%
-- -- so the floor is what stops a submarine-hull manufacturer being alerted
-- about the least irrelevant catering contract available. 0.58 is where the
-- negative control starts leaking into a defence corpus, so that is the floor.

alter table alert_profiles
  add column if not exists match_percentile real not null default 0.99,
  -- Cached because both matching paths need it and one of them runs per
  -- inserted tender, where a corpus-wide percentile is not affordable.
  add column if not exists score_cut real,
  add column if not exists score_cut_refreshed_at timestamptz;

alter table alert_profiles
  drop constraint if exists alert_profiles_match_percentile_range;
alter table alert_profiles
  add constraint alert_profiles_match_percentile_range
  check (match_percentile > 0 and match_percentile < 1);

-- New profiles get the measured floor rather than the number from a provider
-- this service no longer uses. Existing rows still carrying the unreachable
-- default are moved with it; a deliberately customised value is left alone.
alter table alert_profiles alter column min_score set default 0.58;
update alert_profiles set min_score = 0.58 where min_score = 0.72;


-- ---------------------------------------------------------------------------
-- refresh_profile_score_cuts: recompute each active profile's cut from the
-- corpus it will actually be matched against.
--
-- SECURITY DEFINER for the same reason generate_matches_for_tender is: it
-- reads alert_profiles across tenants and writes only each profile's own
-- cached number. It is called by a schedule, never by a user session.
-- ---------------------------------------------------------------------------
create or replace function public.refresh_profile_score_cuts()
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
  v_profile alert_profiles%rowtype;
  v_cut     real;
  v_count   integer := 0;
begin
  for v_profile in
    select * from alert_profiles where is_active and embedding is not null
  loop
    select percentile_cont(v_profile.match_percentile)
             within group (order by (1 - (t.embedding <=> v_profile.embedding)))
      into v_cut
      from tenders t
     where t.embedding is not null
       and t.status in ('open', 'closing_soon')
       and (t.deadline is null or t.deadline > now());

    update alert_profiles
       set score_cut = v_cut,
           score_cut_refreshed_at = now()
     where id = v_profile.id;

    v_count := v_count + 1;
  end loop;

  return v_count;
end;
$$;

comment on function public.refresh_profile_score_cuts() is
  'Recompute each active profile''s similarity cut as a percentile of its own '
  'score distribution over the live corpus. Idempotent; safe on a schedule.';

revoke execute on function public.refresh_profile_score_cuts() from public;
revoke execute on function public.refresh_profile_score_cuts() from anon, authenticated;
grant  execute on function public.refresh_profile_score_cuts() to service_role;


-- ---------------------------------------------------------------------------
-- match_tenders_for_profile: same contract, relative cut.
-- ---------------------------------------------------------------------------
create or replace function match_tenders_for_profile(
  p_profile_id uuid,
  p_since      timestamptz default null,
  p_limit      int default 100
)
returns table (
  tender_id    uuid,
  score        real,
  match_reason jsonb
)
language plpgsql
stable
as $$
declare
  v_profile alert_profiles%rowtype;
  v_cut     real;
begin
  select * into v_profile from alert_profiles where id = p_profile_id;

  if not found then
    raise exception 'alert profile % not found or not visible', p_profile_id;
  end if;

  if v_profile.embedding is null then
    -- No embedding yet means the profile was just created and the service
    -- has not vectorized it. Return nothing rather than matching everything.
    return;
  end if;

  -- Both gates, and the higher one wins. The cached cut may be null on a
  -- profile created since the last refresh, in which case the floor alone
  -- applies -- a new profile should alert conservatively, not not at all.
  v_cut := greatest(v_profile.min_score, coalesce(v_profile.score_cut, 0));

  return query
  with scored as (
    select
      t.id,
      (1 - (t.embedding <=> v_profile.embedding))::real as similarity,
      (
        select coalesce(array_agg(k), '{}')
        from unnest(v_profile.keywords) k
        where t.title  ilike '%' || k || '%'
           or t.summary ilike '%' || k || '%'
           or t.raw_text ilike '%' || k || '%'
      ) as hit_keywords,
      exists (
        select 1
        from unnest(v_profile.excluded_keywords) x
        where t.title ilike '%' || x || '%'
           or t.summary ilike '%' || x || '%'
      ) as excluded
    from tenders t
    where t.embedding is not null
      and t.status in ('open', 'closing_soon')
      and (p_since is null or t.created_at >= p_since)
      and (t.deadline is null or t.deadline > now())
  )
  select
    s.id,
    least(1.0, s.similarity + least(0.1, coalesce(array_length(s.hit_keywords, 1), 0) * 0.02))::real,
    jsonb_build_object(
      'similarity', round(s.similarity::numeric, 4),
      'matched_keywords', to_jsonb(s.hit_keywords),
      'profile_id', p_profile_id,
      -- Recorded so a match can be explained after the fact without
      -- re-deriving which threshold was in force when it fired.
      'cut_applied', round(v_cut::numeric, 4),
      'cut_source', case
        when v_profile.score_cut is null then 'floor'
        when v_profile.score_cut >= v_profile.min_score then 'percentile'
        else 'floor' end
    )
  from scored s
  where not s.excluded
    and s.similarity >= v_cut
  order by 2 desc
  limit p_limit;
end;
$$;


-- ---------------------------------------------------------------------------
-- generate_matches_for_tender: the per-tender path has to agree with the
-- per-profile one, or a tender alerted on insert and a tender found by a
-- later search would answer to two different thresholds.
--
-- This is why the cut is cached on the profile rather than computed live: the
-- trigger runs once per inserted tender, and a corpus-wide percentile per
-- insert would make every ingestion quadratic in the corpus.
-- ---------------------------------------------------------------------------
create or replace function generate_matches_for_tender(p_tender_id uuid)
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
  v_tender     tenders%rowtype;
  v_profile    alert_profiles%rowtype;
  v_similarity real;
  v_cut        real;
  v_hits       text[];
  v_inserted   integer := 0;
begin
  select * into v_tender from tenders where id = p_tender_id;

  if not found or v_tender.embedding is null then
    return 0;
  end if;

  for v_profile in
    select * from alert_profiles where is_active and embedding is not null
  loop
    v_similarity := 1 - (v_tender.embedding <=> v_profile.embedding);
    v_cut := greatest(v_profile.min_score, coalesce(v_profile.score_cut, 0));

    if v_similarity < v_cut then
      continue;
    end if;

    if exists (
      select 1 from unnest(v_profile.excluded_keywords) x
      where v_tender.title ilike '%' || x || '%'
         or coalesce(v_tender.summary, '') ilike '%' || x || '%'
    ) then
      continue;
    end if;

    select coalesce(array_agg(k), '{}') into v_hits
    from unnest(v_profile.keywords) k
    where v_tender.title ilike '%' || k || '%'
       or coalesce(v_tender.summary, '')  ilike '%' || k || '%'
       or coalesce(v_tender.raw_text, '') ilike '%' || k || '%';

    insert into tender_matches (organization_id, profile_id, tender_id, score, match_reason)
    values (
      v_profile.organization_id,
      v_profile.id,
      v_tender.id,
      least(1.0, v_similarity + least(0.1, coalesce(array_length(v_hits, 1), 0) * 0.02)),
      jsonb_build_object(
        'similarity', round(v_similarity::numeric, 4),
        'matched_keywords', to_jsonb(v_hits),
        'profile_name', v_profile.name,
        'cut_applied', round(v_cut::numeric, 4)
      )
    )
    on conflict (profile_id, tender_id) do nothing;

    if found then
      v_inserted := v_inserted + 1;
    end if;
  end loop;

  return v_inserted;
end;
$$;

-- 0008 revoked this from PUBLIC and from anon/authenticated after discovering
-- that `=X` in the ACL string was a live grant. Replacing the function body
-- does not restore those grants, but re-stating them costs nothing and means
-- this file cannot be the one that quietly reopens the door.
revoke execute on function public.generate_matches_for_tender(uuid) from public;
revoke execute on function public.generate_matches_for_tender(uuid) from anon, authenticated;
grant  execute on function public.generate_matches_for_tender(uuid) to service_role;


-- Compute the cuts now, then keep them fresh next to the lifecycle job: the
-- corpus moves every sweep, so a cut measured once is a constant again.
select public.refresh_profile_score_cuts();

select cron.unschedule('refresh-profile-score-cuts')
where exists (select 1 from cron.job where jobname = 'refresh-profile-score-cuts');

select cron.schedule(
  'refresh-profile-score-cuts',
  '22 * * * *',   -- after the lifecycle job at :07, so cuts see current statuses
  $$select public.refresh_profile_score_cuts()$$
);

-- What the new cut does to each profile, so applying this migration shows its
-- own effect instead of requiring a separate query to find out.
select p.name,
       round(p.min_score::numeric, 4)  as floor,
       round(p.score_cut::numeric, 4)  as percentile_cut,
       (select count(*) from tenders t
         where t.embedding is not null
           and t.status in ('open','closing_soon')
           and (1 - (t.embedding <=> p.embedding)) >= greatest(p.min_score, coalesce(p.score_cut, 0))
       ) as would_alert
from alert_profiles p
where p.embedding is not null
order by would_alert desc;
