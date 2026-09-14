-- Fan a profile out across the tenders that already exist.
--
-- 0005 built `generate_matches_for_tender`: a tender arrives, and a trigger
-- scores it against every active profile. That is half the problem, and it is
-- the half that only helps a customer who was already there.
--
-- The missing half is the one every new customer hits first. A company signs
-- up on a Tuesday; the corpus holds 696 tenders harvested before Tuesday;
-- the trigger never fires for any of them, so the new account opens onto an
-- empty page and stays empty until the next sweep brings something in. The
-- product's whole promise is "you never search", and its first impression was
-- going to be nothing to look at.
--
-- The same gap explains why `tender_matches` is empty today. Five profiles
-- exist, the engine returns seven matches each when asked directly, and not
-- one row has ever been written -- because rows are only ever written from
-- the tender side.

-- ---------------------------------------------------------------------------
-- generate_matches_for_profile: the mirror of generate_matches_for_tender.
--
-- Same scoring, same cut, same conflict handling, deliberately: two functions
-- that decide "is this a match" differently would mean a tender surfaced at
-- signup and the same tender surfaced by a later sweep could disagree.
--
-- SECURITY DEFINER for the reason its mirror is: it reads and writes across
-- tenants, keyed to each profile's own organization_id, and is called by the
-- service rather than by a user session.
-- ---------------------------------------------------------------------------
create or replace function public.generate_matches_for_profile(p_profile_id uuid)
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
  v_profile    alert_profiles%rowtype;
  v_tender     record;
  v_similarity real;
  v_cut        real;
  v_hits       text[];
  v_inserted   integer := 0;
begin
  select * into v_profile from alert_profiles where id = p_profile_id;

  if not found or v_profile.embedding is null or not v_profile.is_active then
    return 0;
  end if;

  v_cut := greatest(v_profile.min_score, coalesce(v_profile.score_cut, 0));

  for v_tender in
    select t.id, t.title, t.summary, t.raw_text,
           (1 - (t.embedding <=> v_profile.embedding))::real as similarity
      from tenders t
     where t.embedding is not null
       and t.status in ('open', 'closing_soon')
       and (t.deadline is null or t.deadline > now())
       and (1 - (t.embedding <=> v_profile.embedding)) >= v_cut
     -- Ordered so that a run capped by anything upstream keeps the best
     -- matches rather than an arbitrary slice.
     order by t.embedding <=> v_profile.embedding
  loop
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
      least(1.0, v_tender.similarity + least(0.1, coalesce(array_length(v_hits, 1), 0) * 0.02)),
      jsonb_build_object(
        'similarity', round(v_tender.similarity::numeric, 4),
        'matched_keywords', to_jsonb(v_hits),
        'profile_name', v_profile.name,
        'cut_applied', round(v_cut::numeric, 4),
        -- Distinguishes a match found by fanning a profile over history from
        -- one a trigger produced live. Useful when a customer asks why a
        -- tender from before they signed up is in their feed.
        'origin', 'profile_backfill'
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

comment on function public.generate_matches_for_profile(uuid) is
  'Score one profile against every eligible stored tender and record the '
  'matches. The mirror of generate_matches_for_tender; run it when a profile '
  'is created or re-embedded. Idempotent.';

-- Same two doors as 0008 and 0013. Restated rather than assumed: this file
-- creates a function that writes across tenants, and it must not be the one
-- that leaves it reachable from a browser session.
revoke execute on function public.generate_matches_for_profile(uuid) from public;
revoke execute on function public.generate_matches_for_profile(uuid) from anon, authenticated;
grant  execute on function public.generate_matches_for_profile(uuid) to service_role;


-- ---------------------------------------------------------------------------
-- Backfill every profile that exists now.
-- ---------------------------------------------------------------------------
select p.name,
       public.generate_matches_for_profile(p.id) as matches_written
from alert_profiles p
where p.is_active and p.embedding is not null
order by p.name;

-- Read the result back rather than trusting the counts above: 0013 reported
-- success on an update that changed nothing, and the lesson from it is that a
-- migration should state its end condition and then go and look at it.
select p.name,
       count(m.id) as matches_now,
       round(max(m.score)::numeric, 4) as best_score,
       min(t.deadline)::date as soonest_deadline
from alert_profiles p
left join tender_matches m on m.profile_id = p.id
left join tenders t on t.id = m.tender_id
where p.embedding is not null
group by p.name
order by matches_now desc, p.name;
