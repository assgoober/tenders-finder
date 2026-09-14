-- Run the lifecycle transition on a clock, because that is what it is about.
--
-- 0011 built `age_tender_status()` and argued that a deadline passes on the
-- clock rather than on a sweep. It then left the only caller inside the
-- pipeline, which is the assumption the same ADR said not to rely on.
--
-- Three days later, measured rather than predicted:
--
--   external_id    status         deadline      should be
--   568890-2026    closing_soon   2026-09-13    closed        (5h45m ago)
--   630954-2026    open           2026-09-17    closing_soon
--   569894-2026    open           2026-09-18    closing_soon
--   587343-2026    open           2026-09-20    closing_soon
--
-- A tender that shut six hours ago was sitting in the dashboard labelled
-- "closing soon". Nothing was broken; nothing had run.
--
-- Hourly, not nightly: `closing_soon` is a seven-day window, so an hour of
-- lag is immaterial to it, while `closed` is the label a bid team acts on and
-- a day of lag there is the whole defect. Hourly costs 24 cheap filtered
-- updates a day against a partial index.

-- Supabase ships pg_cron but does not enable it by default. Enabling it is
-- idempotent; it lives in its own schema so nothing in `public` moves.
create extension if not exists pg_cron with schema pg_catalog;

-- Unschedule first so re-running this migration replaces the job instead of
-- failing on the duplicate name, and so editing the schedule is a one-file
-- change rather than a manual delete.
select cron.unschedule('age-tender-status')
where exists (select 1 from cron.job where jobname = 'age-tender-status');

select cron.schedule(
  'age-tender-status',
  '7 * * * *',   -- seven past the hour: off the top, where everything else fires
  $$select public.age_tender_status()$$
);

-- pg_cron runs jobs as the database owner, which already holds execute on the
-- function. Nothing is granted to anon, authenticated or public here -- 0011
-- closed both of those doors and this migration must not quietly reopen one.

-- Catch up whatever drifted while nothing was scheduled.
select * from public.age_tender_status();
