-- Put the lead-time skip on the same clock as everything else it depends on.
--
-- 0018 wrote `skip_late_matches()` to stop pending matches becoming permanent
-- sediment: a tender that closes while its match is still waiting can never
-- be alerted on, because `pending_match_alerts` filters to open tenders --
-- so without a sweep the row stays pending forever, counted and invisible.
--
-- The function was written. It was never scheduled. Measured today:
--
--   matches on a closed tender, still un-alerted and un-skipped   9
--
-- Nine is small. It is also two days old, and it only grows: every match
-- that ages out between manual runs joins it. The fix is not to run the
-- function again -- that fixes nine rows once -- it is to put it on the
-- clock, because the thing it reacts to is the passage of time.
--
-- Two jobs already run hourly, `age-tender-status` at :07 and
-- `refresh-profile-score-cuts` at :22. This one goes at :12, after the
-- aging and before the score cuts:
--
--   :07  a tender that closed since the last hour becomes `closed`
--   :12  matches on it are retired with a reason
--   :22  the profile score cuts are recomputed
--
-- The order matters in one direction only -- the skip reads `deadline`, not
-- `status`, so it is correct whenever it runs -- but running it five minutes
-- after the aging keeps the two consistent within the same hour, which is
-- what anybody reading both tables at once will expect.

create extension if not exists pg_cron with schema pg_catalog;

select cron.unschedule('skip-late-matches')
where exists (select 1 from cron.job where jobname = 'skip-late-matches');

select cron.schedule(
  'skip-late-matches',
  '12 * * * *',
  $$select public.skip_late_matches()$$
);


-- Retire the nine that are already there, so the backlog does not wait an
-- hour for its own fix.
select public.skip_late_matches() as retired_now;


-- End state, read back rather than assumed. `sediment` is the number that
-- matters: matches nobody can ever be told about and nobody has retired.
-- It must be zero, and the hourly job is what keeps it there.
select
  (select count(*) from cron.job where jobname = 'skip-late-matches')       as scheduled,
  (select count(*) from tender_matches where alerted_at is not null)        as delivered,
  (select count(*) from tender_matches where alert_skipped_reason is not null) as skipped,
  (select count(*) from tender_matches where alerted_at is null
     and dismissed_at is null and alert_skipped_reason is null)             as pending,
  (select count(*) from tender_matches m join tenders t on t.id = m.tender_id
     where t.status = 'closed'
       and m.alerted_at is null
       and m.dismissed_at is null
       and m.alert_skipped_reason is null)                                  as sediment;
