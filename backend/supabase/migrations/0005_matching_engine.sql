-- 0005_matching_engine.sql
-- The competitive core: hybrid semantic + keyword matching between newly
-- ingested tenders and each organization's alert profiles.
--
-- Why hybrid and not pure vector search: military and procurement prose uses
-- synonyms freely ("UAV" / "RPAS" / "drone"), which is exactly what
-- embeddings handle and BM25-style keyword matching misses. But embeddings
-- blur precise tokens — MIL-STD-810H, a NATO stock number, a specific alloy
-- grade — which is exactly what keyword matching nails. Each covers the
-- other's failure mode, so the engine runs both and combines them.

-- ---------------------------------------------------------------------------
-- search_tenders: user-facing hybrid search.
-- SECURITY INVOKER on purpose — RLS on `tenders` still applies to the caller.
-- ---------------------------------------------------------------------------
create or replace function search_tenders(
  query_embedding  vector(1024) default null,
  query_text       text         default null,
  filter_status    tender_status default null,
  deadline_after   timestamptz  default null,
  deadline_before  timestamptz  default null,
  match_threshold  real         default 0.0,
  match_count      int          default 50
)
returns table (
  id               uuid,
  title            text,
  summary          text,
  buyer_name       text,
  source_url       text,
  publication_date date,
  deadline         timestamptz,
  status           tender_status,
  metadata         jsonb,
  semantic_score   real,
  keyword_score    real,
  score            real
)
language sql
stable
as $$
  select
    t.id,
    t.title,
    t.summary,
    t.buyer_name,
    t.source_url,
    t.publication_date,
    t.deadline,
    t.status,
    t.metadata,
    coalesce(sem.sim, 0)::real  as semantic_score,
    coalesce(kw.rank, 0)::real  as keyword_score,
    -- 70/30 split: semantics leads, keywords break ties and rescue exact
    -- identifiers the embedding smoothed over.
    (coalesce(sem.sim, 0) * 0.7 + coalesce(kw.rank, 0) * 0.3)::real as score
  from tenders t
  left join lateral (
    select 1 - (t.embedding <=> query_embedding) as sim
    where query_embedding is not null and t.embedding is not null
  ) sem on true
  left join lateral (
    select ts_rank_cd(
             to_tsvector('simple', coalesce(t.title, '') || ' ' || coalesce(t.summary, '')),
             plainto_tsquery('simple', query_text)
           ) as rank
    where query_text is not null and length(trim(query_text)) > 0
  ) kw on true
  where (filter_status   is null or t.status = filter_status)
    and (deadline_after  is null or t.deadline >= deadline_after)
    and (deadline_before is null or t.deadline <= deadline_before)
    and (coalesce(sem.sim, 0) * 0.7 + coalesce(kw.rank, 0) * 0.3) >= match_threshold
  order by score desc, t.deadline asc nulls last
  limit match_count;
$$;

-- ---------------------------------------------------------------------------
-- match_tenders_for_profile: score one organization's profile against the
-- tender corpus. Used both by the ingestion trigger and by the UI ("show me
-- what this profile would have caught").
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

  return query
  with scored as (
    select
      t.id,
      (1 - (t.embedding <=> v_profile.embedding))::real as similarity,
      -- Keywords the tender actually contains, kept for explainability.
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
    -- Each matched keyword adds 2 points of confidence, capped at +0.1, so
    -- keywords can promote a borderline match but never manufacture one.
    least(1.0, s.similarity + least(0.1, coalesce(array_length(s.hit_keywords, 1), 0) * 0.02))::real,
    jsonb_build_object(
      'similarity', round(s.similarity::numeric, 4),
      'matched_keywords', to_jsonb(s.hit_keywords),
      'profile_id', p_profile_id
    )
  from scored s
  where not s.excluded
    and s.similarity >= v_profile.min_score
  order by 2 desc
  limit p_limit;
end;
$$;

-- ---------------------------------------------------------------------------
-- generate_matches_for_tender: fan one new tender out across every active
-- profile in every organization.
--
-- SECURITY DEFINER because it deliberately reads alert_profiles across all
-- tenants — it is called by the ingestion pipeline, never by a user session.
-- It writes only tender_matches rows keyed to each profile's own
-- organization_id, so no data crosses a tenant boundary.
-- ---------------------------------------------------------------------------
create or replace function generate_matches_for_tender(p_tender_id uuid)
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
  v_tender    tenders%rowtype;
  v_profile   alert_profiles%rowtype;
  v_similarity real;
  v_hits      text[];
  v_inserted  integer := 0;
begin
  select * into v_tender from tenders where id = p_tender_id;

  if not found or v_tender.embedding is null then
    return 0;
  end if;

  for v_profile in
    select * from alert_profiles where is_active and embedding is not null
  loop
    v_similarity := 1 - (v_tender.embedding <=> v_profile.embedding);

    if v_similarity < v_profile.min_score then
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
        'profile_name', v_profile.name
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

-- ---------------------------------------------------------------------------
-- Ingestion trigger: the moment a tender gets its embedding, match it.
--
-- Fires on embedding transition (null -> value, or a re-embedding), not on
-- every UPDATE — otherwise a status change would re-run the whole fan-out.
-- pg_notify lets the notification worker push in real time instead of
-- polling; the tender_matches rows are the durable record either way.
-- ---------------------------------------------------------------------------
create or replace function on_tender_embedded()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  v_count integer;
begin
  if new.embedding is null then
    return new;
  end if;

  if tg_op = 'UPDATE'
     and old.embedding is not null
     and old.embedding = new.embedding then
    return new;
  end if;

  v_count := generate_matches_for_tender(new.id);

  if v_count > 0 then
    perform pg_notify(
      'tender_matches',
      json_build_object('tender_id', new.id, 'matches', v_count)::text
    );
  end if;

  return new;
end;
$$;

drop trigger if exists tenders_embedded_match on tenders;
create trigger tenders_embedded_match
  after insert or update of embedding on tenders
  for each row execute function on_tender_embedded();
