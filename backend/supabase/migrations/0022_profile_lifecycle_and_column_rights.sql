-- 0022_profile_lifecycle_and_column_rights.sql
-- Follow-ups from the review of the self-service profile screen (0021 and
-- the /profiles page). Four behaviours and one hole.
--
-- 1. 0021's trigger function did not pin its search_path, unlike every
--    other function in this schema since 0007. Pinned here.
--
-- 2. Resuming a paused profile did not catch up. The tender-side trigger
--    skips inactive profiles, so every tender that arrived during the pause
--    was never scored against it, and nothing ever went back for them.
--    Resuming now clears the embedding exactly as a text edit does (0021),
--    which sends the profile through the one door that already handles
--    this: the sweep re-embeds it, sets its score cut, and backfills it
--    against every open tender. Cost: one embedding request per resume.
--
-- 3. Adding an excluded keyword did not touch matches already made. A
--    customer who adds "catering" expects catering tenders to leave their
--    feed, not only to stop arriving. Matches that the new exclusion would
--    have prevented are DISMISSED, not deleted: the feed hides dismissed
--    matches, and a dismissal keeps the record of what was sent. Same text
--    test the matcher applies (title or summary, ILIKE).
--
-- 4. `has_embedding`: the profiles page only needs to know whether a vector
--    exists, and was fetching all 1024 floats per profile to find out.
--
-- 5. The hole: `alert_profiles_rw` is FOR ALL, so a member could, with their
--    own session and the public anon key, DELETE a profile (cascading away
--    its match history, which the UI deliberately never offers) or write
--    `embedding`, `score_cut` and `min_score` directly -- values the matcher
--    trusts and that only the service is meant to produce. Still tenant-
--    scoped, so never another company's rows; but "pause, never delete" was
--    enforced by a button, not by the database. Table privileges now grant
--    authenticated users exactly the columns the screen writes, and no
--    DELETE. RLS still decides WHICH rows; this decides WHAT may be done to
--    them. The service role is untouched.

-- 1 + 2 ----------------------------------------------------------------------

create or replace function public.alert_profile_text_changed()
returns trigger
language plpgsql
set search_path = public
as $$
begin
  if new.description is distinct from old.description
     -- Resumed: tenders that arrived while paused were never scored
     -- against this profile. Re-entering the embed queue backfills them.
     or (new.is_active and not old.is_active) then
    new.embedding := null;
    new.score_cut := null;
  end if;
  return new;
end;
$$;

comment on function public.alert_profile_text_changed() is
  'Clears embedding and score_cut when an alert profile''s description changes or it is resumed, so the sweep re-embeds and backfills it.';

-- 3 --------------------------------------------------------------------------

create or replace function public.dismiss_newly_excluded_matches()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  -- Only keywords that were not excluded before; re-saving an unchanged
  -- list must not re-run anything.
  update tender_matches m
     set dismissed_at = now()
    from tenders t
   where m.profile_id = new.id
     and m.tender_id = t.id
     and m.dismissed_at is null
     and exists (
       select 1
         from unnest(new.excluded_keywords) x
        where not (x = any (old.excluded_keywords))
          and (t.title ilike '%' || x || '%'
               or coalesce(t.summary, '') ilike '%' || x || '%')
     );
  return null;
end;
$$;

-- SECURITY DEFINER because it is a trigger acting for the profile's own
-- organization (it touches only `new.id`'s matches); nobody calls it directly.
revoke execute on function public.dismiss_newly_excluded_matches() from public;
revoke execute on function public.dismiss_newly_excluded_matches() from anon, authenticated;

drop trigger if exists alert_profiles_dismiss_excluded on alert_profiles;
create trigger alert_profiles_dismiss_excluded
  after update of excluded_keywords on alert_profiles
  for each row
  when (new.excluded_keywords is distinct from old.excluded_keywords)
  execute function public.dismiss_newly_excluded_matches();

-- 4 --------------------------------------------------------------------------

alter table alert_profiles
  add column if not exists has_embedding boolean
  generated always as (embedding is not null) stored;

-- 5 --------------------------------------------------------------------------

revoke insert, update, delete on alert_profiles from anon, authenticated;

grant insert (organization_id, name, description, keywords, excluded_keywords,
              notify_email, is_active, created_by)
  on alert_profiles to authenticated;

grant update (name, description, keywords, excluded_keywords, notify_email, is_active)
  on alert_profiles to authenticated;

-- What the column grants now permit, read back so a run that did not take
-- is visible rather than silent.
select privilege_type, string_agg(column_name, ', ' order by column_name) as columns
  from information_schema.column_privileges
 where table_name = 'alert_profiles' and grantee = 'authenticated'
   and privilege_type in ('INSERT', 'UPDATE')
 group by privilege_type;
