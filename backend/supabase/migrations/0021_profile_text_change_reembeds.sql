-- 0021_profile_text_change_reembeds.sql
-- A profile whose words change must stop matching on the old words.
--
-- Until now an alert profile was written once, by hand, with the service
-- key, and its embedding was computed straight after. The dashboard now lets
-- a customer edit their own profile, and that opens a gap the hand process
-- never had: the description changes, the embedding does not, and the
-- profile goes on matching -- silently and with full confidence -- against
-- the text the customer just deleted. Nothing would look wrong. The alerts
-- would simply be about something else.
--
-- Clearing the vector is how a profile re-enters the queue:
-- `embed_pending_profiles()` picks up every profile whose embedding is null,
-- embeds it, and backfills its matches (`generate_matches_for_profile`). The
-- score cut is cleared with it, because a percentile computed for the old
-- vector says nothing about the new one; `refresh_profile_score_cuts()`
-- recomputes it on its schedule, and `min_score` holds the floor until then.
--
-- Matches already found are kept. They were correct for the profile as it
-- stood when they were found, and a customer may already have acted on them.

create or replace function public.alert_profile_text_changed()
returns trigger
language plpgsql
as $$
begin
  if new.description is distinct from old.description then
    new.embedding := null;
    new.score_cut := null;
  end if;
  return new;
end;
$$;

drop trigger if exists alert_profiles_reembed_on_text_change on alert_profiles;
create trigger alert_profiles_reembed_on_text_change
  before update on alert_profiles
  for each row execute function public.alert_profile_text_changed();

comment on function public.alert_profile_text_changed() is
  'Clears embedding and score_cut when an alert profile''s description changes, so the profile is re-embedded instead of matching on text that no longer exists.';
