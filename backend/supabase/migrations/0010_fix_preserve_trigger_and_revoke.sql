-- 0010_fix_preserve_trigger_and_revoke.sql
-- Two corrections to 0009. Both were found by checking the result rather than
-- the "Success. No rows returned" the editor reported.
--
-- ---------------------------------------------------------------------------
-- 1. The audit record recorded the wrong values.
-- ---------------------------------------------------------------------------
--
-- 0009 preserves a stored extraction when a failed attempt would overwrite it,
-- and writes what was attempted to `metadata.extraction.last_failed_attempt`
-- so the block is auditable instead of magic. Tested end to end -- a real
-- extraction, then a simulated 429 write over it -- the protection held and
-- the audit record read:
--
--     {"attempted_status": "extracted", "error": "", "status": "extracted"}
--
-- It should have said `failed` and carried the 429 text. The trigger restores
-- `new.extraction_status` and `new.extraction_error` from OLD, and only then
-- builds the JSON *from those same NEW fields* -- which by that point hold the
-- restored values, not the attempted ones. An ordering mistake, and its output
-- is the worst kind: a log that looks like a record of what happened and
-- contains none of it. A run that could not re-extract anything would have
-- left no trace anywhere.
--
-- Fixed by capturing the attempt into locals before restoring anything.
--
-- ---------------------------------------------------------------------------
-- 2. The revoke in 0009 closed half the door.
-- ---------------------------------------------------------------------------
--
-- 0009 ends with `revoke execute ... from public` and a comment claiming the
-- function is "not granted to anon or authenticated". The ACL afterwards:
--
--     postgres=X/postgres | anon=X/postgres | authenticated=X/postgres | service_role=X/postgres
--
-- PUBLIC (`=X/postgres`) is gone, so that revoke worked -- but `anon` and
-- `authenticated` hold their own explicit grants, which Supabase attaches to
-- every new function in `public` via ALTER DEFAULT PRIVILEGES.
--
-- This is the mirror image of 0007/0008, and the two together are the whole
-- rule:
--
--   0008: revoking the named roles left PUBLIC, which every role inherits.
--   0009: revoking PUBLIC left the named grants.
--
--   Closed means BOTH:
--       revoke execute on function f() from public, anon, authenticated;
--
--   And confirmed only by has_function_privilege(), never by reading the ACL
--   string -- neither mistake is visible there unless you already know that a
--   bare `=X` means PUBLIC.
--
-- Exposure here is nil either way: the function returns `trigger`, so
-- PostgREST cannot publish it. Corrected because an applied migration whose
-- comment contradicts the database is the drift this project keeps paying to
-- remove, and because the next function to copy this pattern may return a
-- scalar.

create or replace function preserve_successful_extraction()
returns trigger
language plpgsql
security invoker
set search_path = public
as $$
declare
  -- Captured before anything is restored. This is the entire fix: read the
  -- attempt while it is still on the row.
  attempted_status text := coalesce(new.extraction_status::text, 'unknown');
  attempted_error  text := left(coalesce(new.extraction_error, ''), 500);
begin
  if old.extraction_status = 'extracted'
     and new.extraction_status is distinct from 'extracted' then

    new.extraction_status := old.extraction_status;
    new.extraction_model  := old.extraction_model;
    new.extraction_error  := old.extraction_error;
    new.summary           := old.summary;
    new.metadata          := old.metadata;
    new.embedding         := old.embedding;
    new.buyer_name        := coalesce(new.buyer_name, old.buyer_name);
    new.currency          := coalesce(new.currency, old.currency);
    new.estimated_value   := coalesce(new.estimated_value, old.estimated_value);

    new.metadata := jsonb_set(
      coalesce(new.metadata, '{}'::jsonb),
      '{extraction,last_failed_attempt}',
      jsonb_build_object(
        'at',               to_jsonb(now()),
        'attempted_status', to_jsonb(attempted_status),
        'error',            to_jsonb(attempted_error),
        'preserved_status', to_jsonb(old.extraction_status::text)
      ),
      true
    );

    raise log 'preserve_successful_extraction: kept the stored extraction for tender % (attempt was %)',
      old.id, attempted_status;
  end if;

  return new;
end;
$$;

revoke execute on function public.preserve_successful_extraction()
  from public, anon, authenticated;

-- Verify the result, not the editor's message:
--
--   select has_function_privilege('anon','public.preserve_successful_extraction()','EXECUTE');
--   -- expected: false
--
-- and re-run the behavioural check: extract a tender, write a failed result
-- over it, then confirm extraction_status is still 'extracted' AND that
-- metadata.extraction.last_failed_attempt names the attempted status and error.
