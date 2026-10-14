-- 0013 tried to move every profile off the unreachable floor and moved none.
--
--   update alert_profiles set min_score = 0.58 where min_score = 0.72;
--
-- It ran without error, reported success, and matched zero rows. The column
-- is `real`; the literal is `numeric`. Postgres promotes the column to
-- numeric to compare them, and float4 0.72 is 0.7200000286102295, which is
-- not numeric 0.72:
--
--   min_score = 0.72          ->  false
--   min_score = 0.72::real    ->  true
--   abs(min_score - 0.72) < 1e-6 -> true
--
-- So after 0013 every profile still carried floor 0.7200 and would_alert 0 --
-- the exact condition 0013 exists to end. Nothing failed. A float was
-- compared for exact equality across two numeric types, which is the same
-- shape as every other defect recorded in REPOMIX: a plausible statement that
-- silently does nothing.

-- Tolerance, not equality. 1e-6 is far larger than float4's error at this
-- magnitude and far smaller than any threshold anyone would deliberately set,
-- so it can only match the value it is meant to.
update alert_profiles
   set min_score = 0.58
 where abs(min_score - 0.72) < 1e-6;

-- The default was DDL rather than a comparison, so it did take -- restated
-- here only so this file describes the whole intended end state rather than
-- half of it.
alter table alert_profiles alter column min_score set default 0.58;

-- Cuts are computed from the corpus, not from min_score, so they are still
-- valid. Refreshed anyway: cheap, idempotent, and it means applying this file
-- leaves nothing stale behind it.
select public.refresh_profile_score_cuts();

-- Read the end state back instead of trusting that the update above did
-- something. Anything with floor 0.72 still in this list means this migration
-- failed the same way 0013 did.
select p.name,
       round(p.min_score::numeric, 4) as floor,
       round(p.score_cut::numeric, 4) as percentile_cut,
       (select count(*) from tenders t
         where t.embedding is not null
           and t.status in ('open','closing_soon')
           and (t.deadline is null or t.deadline > now())
           and (1 - (t.embedding <=> p.embedding))
               >= greatest(p.min_score, coalesce(p.score_cut, 0))
       ) as would_alert
from alert_profiles p
where p.embedding is not null
order by would_alert desc, p.name;
