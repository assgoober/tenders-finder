-- Age a tender's status against the clock.
--
-- `tenders.status` has defaulted to 'open' since 0003 and nothing has ever
-- moved it. Measured on 2026-09-09: 168 rows, every one of them 'open', of
-- which 46 had a deadline in the past and 60 were closing inside a week. A
-- dashboard built on that column tells a bid team a tender is open eight days
-- after it shut, which is the exact failure this product exists to prevent.
--
-- The transition lives in the database rather than in the scraper because it
-- has to be true continuously, not only in the minutes after a sweep. A row
-- whose deadline passes at 14:00 is stale at 14:01 whether or not anything ran.
--
-- Deliberately a pure function of `deadline`, so it is idempotent and
-- self-correcting: a portal that extends a deadline moves the row back to
-- 'open' on the next call rather than leaving it wrongly closed.

-- 'awarded' and 'cancelled' are human or portal decisions about the outcome of
-- a tender, not statements about the clock. They are terminal, and nothing
-- here may overwrite them -- that is the whole reason this is a filtered
-- update rather than a generated column.
create or replace function public.age_tender_status(
  closing_soon_window interval default interval '7 days'
)
returns table (to_open bigint, to_closing_soon bigint, to_closed bigint)
language plpgsql
security definer
set search_path = public
as $$
declare
  moved_open         bigint := 0;
  moved_closing_soon bigint := 0;
  moved_closed       bigint := 0;
begin
  with target as (
    select
      id,
      case
        -- A tender with no published deadline says nothing about the clock,
        -- so the clock says nothing about it. Left exactly as found.
        when deadline is null                       then status
        when deadline < now()                       then 'closed'::tender_status
        when deadline <= now() + closing_soon_window then 'closing_soon'::tender_status
        else                                             'open'::tender_status
      end as want
    from tenders
    where status in ('open', 'closing_soon', 'closed')
  ),
  changed as (
    update tenders t
       set status = target.want
      from target
     where t.id = target.id
       and t.status is distinct from target.want
    returning target.want as want
  )
  select
    count(*) filter (where want = 'open'),
    count(*) filter (where want = 'closing_soon'),
    count(*) filter (where want = 'closed')
  into moved_open, moved_closing_soon, moved_closed
  from changed;

  return query select moved_open, moved_closing_soon, moved_closed;
end;
$$;

comment on function public.age_tender_status(interval) is
  'Move tenders between open / closing_soon / closed from their deadline. '
  'Never touches awarded or cancelled. Idempotent; safe to call on a schedule.';

-- Same lesson as 0008: a bare `revoke ... from anon, authenticated` leaves the
-- `=X` grant to PUBLIC in place, and has_function_privilege still answers true.
-- Both doors, then one explicit grant back.
revoke execute on function public.age_tender_status(interval) from public;
revoke execute on function public.age_tender_status(interval) from anon, authenticated;
grant  execute on function public.age_tender_status(interval) to service_role;

-- Partial index for the scan this function and the dashboard both do. Only the
-- rows the clock can still move, so it stays small as the corpus grows.
create index if not exists tenders_status_deadline_idx
  on public.tenders (status, deadline)
  where status in ('open', 'closing_soon', 'closed');

-- One-time correction for the backlog that accumulated while nothing aged it.
select * from public.age_tender_status();
