-- 0009_preserve_good_extractions.sql
-- Stop a transient failure from erasing an extraction that already succeeded.
--
-- Observed, not anticipated. A sweep hit the Gemini free tier's per-day
-- request cap and came back `429 RESOURCE_EXHAUSTED` for every tender. The
-- pipeline recorded each as `failed` and upserted it, and because the upsert
-- writes the latest attempt unconditionally, three tenders that had extracted
-- correctly an hour earlier were overwritten: `summary` nulled, PEGS metadata
-- gone, `extraction_status` downgraded to `failed`.
--
-- ADR-2 promises that unvalidated output never reaches the database. Nobody
-- had promised the other direction -- that validated output cannot be *removed*
-- by a network error. This is that promise, and it is enforced here rather
-- than in the pipeline for the same reason ADR-15 moved the alert check into
-- the component that renders alerts: the guarantee has to live where the data
-- actually changes. A backfill script, a manual fix in the SQL editor, or a
-- second ingestion path would each bypass an application-level check.
--
-- What it is NOT: a way to hide failures. The failed attempt is recorded on
-- the surviving row under `metadata.extraction.last_failed_attempt`, so a run
-- that could not re-extract anything is still visible -- it just is not
-- destructive. Silently keeping the old value and saying nothing would trade
-- one invisible problem for another.

create or replace function preserve_successful_extraction()
returns trigger
language plpgsql
security invoker
set search_path = public
as $$
begin
  -- Only interferes in one direction: a stored success being replaced by a
  -- non-success. Success -> success overwrites normally (a re-extraction is
  -- allowed to improve a row), and failure -> anything is untouched.
  if old.extraction_status = 'extracted'
     and new.extraction_status is distinct from 'extracted' then

    -- Keep everything the successful pass produced.
    new.extraction_status := old.extraction_status;
    new.extraction_model  := old.extraction_model;
    new.extraction_error  := old.extraction_error;
    new.summary           := old.summary;
    new.metadata          := old.metadata;
    new.embedding         := old.embedding;
    new.buyer_name        := coalesce(new.buyer_name, old.buyer_name);
    new.currency          := coalesce(new.currency, old.currency);
    new.estimated_value   := coalesce(new.estimated_value, old.estimated_value);

    -- ...and leave a record of the attempt that was refused, so this is
    -- auditable rather than magic. Written onto the preserved metadata, after
    -- it is restored above.
    new.metadata := jsonb_set(
      coalesce(new.metadata, '{}'::jsonb),
      '{extraction,last_failed_attempt}',
      jsonb_build_object(
        'at', to_jsonb(now()),
        'status', to_jsonb(old.extraction_status),
        'attempted_status', to_jsonb(coalesce(new.extraction_status::text, 'unknown')),
        'error', to_jsonb(left(coalesce(new.extraction_error, ''), 500))
      ),
      true
    );

    raise log 'preserve_successful_extraction: kept the stored extraction for tender %', old.id;
  end if;

  return new;
end;
$$;

drop trigger if exists tenders_preserve_extraction on tenders;
create trigger tenders_preserve_extraction
  before update on tenders
  for each row
  execute function preserve_successful_extraction();

-- Not granted to anon or authenticated: nothing outside the service role
-- writes this table, and a PUBLIC EXECUTE grant on a new function is the
-- default that migration 0008 had to undo. Revoked at creation this time.
revoke execute on function public.preserve_successful_extraction() from public;
