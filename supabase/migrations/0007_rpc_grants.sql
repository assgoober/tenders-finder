-- 0007_rpc_grants.sql
-- Close an RPC that PostgREST exposes to unauthenticated callers.
--
-- Found by the Supabase database linter against the live project, not by
-- reading the migrations: `generate_matches_for_tender(uuid)` is
-- SECURITY DEFINER, returns a scalar, and `anon` holds EXECUTE on it. Those
-- three facts together mean PostgREST publishes it at
-- `/rest/v1/rpc/generate_matches_for_tender`, and the anon key that reaches
-- it is printed in the page source of every browser client.
--
-- So an unauthenticated caller could run a function that writes
-- `tender_matches` rows with RLS bypassed, and make the database perform a
-- vector search across every alert profile while doing it. Neither is a data
-- leak — the function returns a count — but both are effects that should
-- require a login, and the second is cheap to repeat.
--
-- `generate_matches_for_tender` is called from `on_tender_embedded`, a
-- trigger that fires when the pipeline writes an embedding. Triggers run as
-- the statement's executor and do not consult EXECUTE grants the way an RPC
-- does, so revoking these grants does not disturb the path the function
-- actually exists for. `service_role` keeps EXECUTE for the same reason the
-- scraper has it: it is the caller that legitimately backfills.

revoke execute on function public.generate_matches_for_tender(uuid) from anon, authenticated;

-- `search_tenders` and `match_tenders_for_profile` are deliberately NOT
-- revoked. They are SECURITY INVOKER, so they read through the caller's own
-- RLS policies rather than around them, and the dashboard calls them as the
-- signed-in user. Revoking them would break the feature they exist for.
-- What they are missing is a pinned search_path, which is set below.

-- ---------------------------------------------------------------------------
-- Pin search_path on the remaining project functions.
--
-- A function without `SET search_path` resolves unqualified names through
-- whatever path the caller brings. On a SECURITY DEFINER function that is a
-- privilege-escalation route; on these three, all SECURITY INVOKER, it is
-- the milder problem of a function that can be made to resolve a different
-- table than the one its author meant. Pinning costs nothing and removes the
-- question.
-- ---------------------------------------------------------------------------

-- Signatures read from the live catalogue with pg_get_function_identity_arguments,
-- not from the migration source. `search_tenders` takes seven parameters and
-- one of them is the project's own `tender_status` enum, which is the kind of
-- detail a guessed signature gets wrong and a failed ALTER then reports as a
-- missing function.
alter function public.set_updated_at()
  set search_path = public;

alter function public.match_tenders_for_profile(uuid, timestamptz, integer)
  set search_path = public;

alter function public.search_tenders(
  vector, text, tender_status, timestamptz, timestamptz, real, integer
) set search_path = public;
