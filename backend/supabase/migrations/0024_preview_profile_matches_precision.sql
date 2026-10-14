-- 0024_preview_profile_matches_precision.sql
-- Precision fix for preview_profile_matches (0023), which is already applied
-- and therefore not edited.
--
-- Production (0013/0015) computes the percentile over the DOUBLE PRECISION
-- expression 1 - (embedding <=> e), stores it in a `real` column (score_cut),
-- and then compares the DOUBLE similarity against that real cut. 0023 cast
-- the similarity to real first and compared real >= real, which at a boundary
-- can count a tender the sweep would not: the failure mode 0014 fixed for
-- min_score. This version keeps similarity as double, rounds the percentile
-- to real exactly as storing it in score_cut does, and compares double to
-- real (real promoted to double), as generate_matches_for_profile does.
--
-- MIRRORS, deliberately:
--   * generate_matches_for_profile (0015): open/closing_soon tenders with a
--     future (or no) deadline, similarity >= cut, excluded keywords vetoed on
--     title or summary;
--   * refresh_profile_score_cuts (0013): cut = percentile of similarity over
--     those same tenders, floored by min_score.
-- A change to either must be made here too. The constants model a NEW
-- profile's defaults: min_score 0.58 (0013/0014), match_percentile 0.99 (0013).
--
-- SECURITY INVOKER: reads only `tenders` (open to signed-in users by
-- `tenders_read`). Writes nothing.

create or replace function public.preview_profile_matches(
  p_embedding         vector(1024),
  p_excluded_keywords text[]  default '{}',
  p_limit             integer default 3
)
returns jsonb
language sql
stable
security invoker
set search_path = public
as $$
  with open_tenders as (
    select t.id, t.title, t.summary, t.deadline,
           (1 - (t.embedding <=> p_embedding)) as similarity
      from tenders t
     where t.embedding is not null
       and t.status in ('open', 'closing_soon')
       and (t.deadline is null or t.deadline > now())
  ),
  cut as (
    select greatest(
             0.58::real,
             coalesce(percentile_cont(0.99) within group (order by similarity), 0)::real
           ) as value
      from open_tenders
  ),
  hits as (
    select o.*
      from open_tenders o, cut
     where o.similarity >= cut.value
       and not exists (
         select 1 from unnest(coalesce(p_excluded_keywords, '{}')) x
          where o.title ilike '%' || x || '%'
             or coalesce(o.summary, '') ilike '%' || x || '%'
       )
  )
  select jsonb_build_object(
    'open_tenders', (select count(*) from open_tenders),
    'total',        (select count(*) from hits),
    'cut',          (select value from cut),
    'top', coalesce((
      select jsonb_agg(jsonb_build_object(
               'id', h.id, 'title', h.title, 'deadline', h.deadline,
               'similarity', round(h.similarity::numeric, 4))
             order by h.similarity desc, h.id)
        from (select * from hits
               order by similarity desc, id
               limit greatest(0, least(coalesce(p_limit, 3), 10))) h
    ), '[]'::jsonb)
  );
$$;

comment on function public.preview_profile_matches(vector, text[], integer) is
  'What a draft profile would match right now (new-profile defaults: min_score '
  '0.58, percentile 0.99). Read-only mirror of generate_matches_for_profile and '
  'refresh_profile_score_cuts, with the same double/real precision.';

revoke execute on function public.preview_profile_matches(vector, text[], integer) from public;
revoke execute on function public.preview_profile_matches(vector, text[], integer) from anon;
grant  execute on function public.preview_profile_matches(vector, text[], integer) to authenticated, service_role;
