-- 0023_preview_profile_matches.sql
-- What a draft profile WOULD match right now — for the onboarding chat's
-- preview, before anything is saved.
--
-- MIRRORS, deliberately and line for line:
--   * generate_matches_for_profile (0015): open/closing_soon tenders with a
--     future (or no) deadline, similarity >= cut, excluded keywords vetoed on
--     title or summary;
--   * refresh_profile_score_cuts (0013): cut = percentile of similarity over
--     those same tenders, floored by min_score.
-- A change to either of those functions must be made here too, or the
-- preview will promise matches the sweep does not deliver. The constants are
-- the column defaults a new profile gets: min_score 0.58 (0013/0014) and
-- match_percentile 0.99 (0013).
--
-- SECURITY INVOKER: it reads only `tenders`, which `tenders_read` already
-- opens to any signed-in user. It writes nothing.

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
           (1 - (t.embedding <=> p_embedding))::real as similarity
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
             order by h.similarity desc)
        from (select * from hits
               order by similarity desc
               limit greatest(0, least(coalesce(p_limit, 3), 10))) h
    ), '[]'::jsonb)
  );
$$;

revoke execute on function public.preview_profile_matches(vector, text[], integer) from public;
revoke execute on function public.preview_profile_matches(vector, text[], integer) from anon;
grant  execute on function public.preview_profile_matches(vector, text[], integer) to authenticated, service_role;
