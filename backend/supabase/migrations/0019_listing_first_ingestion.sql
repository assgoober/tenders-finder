-- Let a tender enter the matching corpus from its listing alone.
--
-- The wall this removes, measured rather than assumed:
--
--   TED, contract notices, 30-day window
--     classification-cpv IN (35000000)            594   <- all we ingest
--     the same query across 15 defence-adjacent divisions  11,664
--
-- Twenty times more procurement, from an API we already read, behind one
-- line of configuration. We do not take it because of a rule in the
-- pipeline: a tender receives its embedding from `extraction.embedding_text()`
-- and nowhere else, so no tender can be matched until an LLM has read it.
-- On 2026-09-11 a sweep of 551 notices aborted on quota at 483. The ceiling
-- is not the portal. It is one model call per tender, paid before we know
-- whether anyone wants the tender at all.
--
-- The inversion: a listing is the buyer's own words -- title, description,
-- CPV codes, deadline, published by the contracting authority. An extraction
-- is a model's reading of those words. We were gating the corpus on the less
-- trustworthy of the two and calling it caution.
--
-- So a tender is now embedded from its listing at ingestion, cheaply, and
-- the full PEGS extraction is deferred until the tender actually matches
-- somebody's profile. Matching gets wide; extraction stays expensive and
-- rare. ADR-2 is untouched: no unvalidated *model output* reaches the
-- database, because at this stage there is no model output at all.
--
-- No new extraction_status value. A listing-only row is `pending`, which is
-- what the column already means and already defaults to; its embedding
-- carries `metadata.embedding.source = 'listing'` to say where the vector
-- came from. Adding an enum value would have meant two migrations -- Postgres
-- refuses to use a new enum label in the transaction that created it -- and
-- we have already spent one failed migration this week on exactly that class
-- of rule.


-- ---------------------------------------------------------------------------
-- A row must never lose the embedding it already has.
--
-- 0009 stopped a transient failure from erasing a successful extraction. It
-- checked `old.extraction_status = 'extracted'`, which was the whole story
-- while an embedding could only come from an extraction.
--
-- It is not the whole story now. A listing-embedded row is `pending`, so it
-- falls outside that check entirely: the first deferred extraction that fails
-- -- one 429, one malformed PDF -- would upsert `embedding = null` over a
-- perfectly good listing vector and drop the tender out of the corpus. The
-- tender would still be in the table, still be open, and silently stop
-- matching anything. That is precisely the failure 0009 was written to
-- prevent, arriving through a door 0009 could not see.
-- ---------------------------------------------------------------------------
create or replace function preserve_successful_extraction()
returns trigger
language plpgsql
security invoker
set search_path = public
as $$
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
        'at', to_jsonb(now()),
        'status', to_jsonb(old.extraction_status),
        'attempted_status', to_jsonb(coalesce(new.extraction_status::text, 'unknown')),
        'error', to_jsonb(left(coalesce(new.extraction_error, ''), 500))
      ),
      true
    );
  end if;

  -- The new half, and deliberately unconditional. Whatever the statuses say,
  -- a write that would replace a vector with nothing is refused: being
  -- matchable is a property the row has already earned, and no later attempt
  -- gets to take it away by failing. An upsert that carries a *better*
  -- embedding still replaces the old one -- only null is refused.
  if new.embedding is null and old.embedding is not null then
    new.embedding := old.embedding;
    new.metadata := jsonb_set(
      coalesce(new.metadata, '{}'::jsonb),
      '{embedding,preserved_at}',
      to_jsonb(now()),
      true
    );
  end if;

  return new;
end;
$$;

comment on function preserve_successful_extraction() is
  'Protects two things a later write must never destroy: a successful '
  'extraction, and any embedding at all. A tender without an embedding is '
  'invisible to the alert engine, so losing one is a silent outage.';


-- ---------------------------------------------------------------------------
-- The deferred-extraction work queue.
--
-- Which tenders have earned the cost of a model call? The ones somebody's
-- profile actually matched. This returns them worst-first by deadline, so a
-- budget that runs out runs out on the tenders with the most time left.
--
-- `min_score` deliberately takes a floor rather than reading each profile's
-- cut: this is a spending decision about a tender, and a tender matched by
-- any profile at all is worth reading once.
-- ---------------------------------------------------------------------------
create or replace function public.tenders_awaiting_extraction(
  p_limit integer default 100,
  p_min_score real default 0.5
)
returns table (
  tender_id   uuid,
  external_id text,
  source_url  text,
  title       text,
  deadline    timestamptz,
  best_score  real,
  matches     integer
)
language sql
stable
security definer
set search_path = public
as $$
  select
    t.id,
    t.external_id,
    t.source_url,
    t.title,
    t.deadline,
    max(m.score)::real,
    count(*)::integer
  from tenders t
  join tender_matches m on m.tender_id = t.id
  where t.extraction_status = 'pending'
    and t.status in ('open', 'closing_soon')
    and m.score >= p_min_score
    and m.dismissed_at is null
  group by t.id, t.external_id, t.source_url, t.title, t.deadline
  order by t.deadline asc nulls last, max(m.score) desc
  limit p_limit;
$$;

comment on function public.tenders_awaiting_extraction(integer, real) is
  'Tenders that somebody matched but nobody has read yet. The queue that '
  'decides where the extraction budget goes.';

revoke execute on function public.tenders_awaiting_extraction(integer, real) from public;
revoke execute on function public.tenders_awaiting_extraction(integer, real) from anon, authenticated;
grant  execute on function public.tenders_awaiting_extraction(integer, real) to service_role;


-- What this looks like right now. `listing_embedded` is 0 before the first
-- listing-first sweep -- that is the correct reading, not a failure.
select
  (select count(*) from tenders)                                        as tenders,
  (select count(*) from tenders where embedding is not null)            as matchable,
  (select count(*) from tenders where extraction_status = 'extracted')  as extracted,
  (select count(*) from tenders where extraction_status = 'pending')    as pending,
  (select count(*) from tenders
     where metadata->'embedding'->>'source' = 'listing')                as listing_embedded,
  (select count(*) from public.tenders_awaiting_extraction(1000))       as queued_for_extraction;
