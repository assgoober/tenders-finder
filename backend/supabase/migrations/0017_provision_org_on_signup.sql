-- Give a new account somewhere to live.
--
-- Every tenant-scoped row in this schema is keyed to an organization_id, and
-- every RLS policy resolves through auth_org_ids(), which reads
-- organization_members. A user who signs up and belongs to no organisation
-- therefore sees nothing at all -- not an error, not an empty state anyone
-- designed, just every query returning zero rows forever.
--
-- Until now that was hidden because the only account in the database was
-- created by hand, with its organisation and membership inserted by hand
-- alongside it. The first real sign-up would have produced a working login to
-- an invisible product.
--
-- Provisioning happens in a trigger rather than in the web app on purpose.
-- The app can only do it in a second request, after the first one already
-- created the user, and anything between those two requests -- a closed tab,
-- a dropped connection, a failed deploy -- leaves an account that can log in
-- and can never see anything. In a trigger it is the same transaction as the
-- user row: both, or neither.

-- ---------------------------------------------------------------------------
-- A slug that is stable, readable and unique without a retry loop.
-- ---------------------------------------------------------------------------
create or replace function public.org_slug_from(p_name text, p_seed uuid)
returns text
language sql
immutable
as $$
  select
    coalesce(
      nullif(
        -- Latin letters, digits and single hyphens. A Hebrew or Greek company
        -- name reduces to nothing here, which is why the id suffix is not
        -- optional.
        trim(both '-' from regexp_replace(lower(p_name), '[^a-z0-9]+', '-', 'g')),
        ''
      ),
      'org'
    ) || '-' || left(replace(p_seed::text, '-', ''), 6);
$$;


-- ---------------------------------------------------------------------------
-- handle_new_user: one organisation, one owner, in the same transaction as
-- the account.
-- ---------------------------------------------------------------------------
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  v_company text;
  v_org_id  uuid;
begin
  -- The sign-up form sends company_name in the user metadata. Falling back to
  -- the email's local part rather than refusing: an account that exists and
  -- is named badly is recoverable, an account that failed to provision is a
  -- support ticket.
  v_company := coalesce(
    nullif(trim(new.raw_user_meta_data->>'company_name'), ''),
    nullif(split_part(coalesce(new.email, ''), '@', 1), ''),
    'New organisation'
  );

  insert into organizations (name, slug)
  values (v_company, public.org_slug_from(v_company, new.id))
  returning id into v_org_id;

  insert into organization_members (organization_id, user_id, role)
  values (v_org_id, new.id, 'owner');

  return new;
exception
  when others then
    -- Never block a sign-up over provisioning. An account with no
    -- organisation is visible and fixable; a sign-up that returns an error to
    -- a prospective customer is gone.
    raise warning 'could not provision an organisation for %: %', new.id, sqlerrm;
    return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();


-- ---------------------------------------------------------------------------
-- Backfill: any existing user with no organisation would be invisible too.
-- ---------------------------------------------------------------------------
do $$
declare
  v_user record;
  v_org  uuid;
  v_name text;
begin
  for v_user in
    select u.id, u.email, u.raw_user_meta_data
      from auth.users u
     where not exists (
       select 1 from organization_members m where m.user_id = u.id
     )
  loop
    v_name := coalesce(
      nullif(trim(v_user.raw_user_meta_data->>'company_name'), ''),
      nullif(split_part(coalesce(v_user.email, ''), '@', 1), ''),
      'New organisation'
    );
    insert into organizations (name, slug)
    values (v_name, public.org_slug_from(v_name, v_user.id))
    returning id into v_org;
    insert into organization_members (organization_id, user_id, role)
    values (v_org, v_user.id, 'owner');
  end loop;
end;
$$;


-- End state, read back rather than assumed: no account may be left without an
-- organisation, because an account without one can log in and see nothing.
select
  (select count(*) from auth.users) as users,
  (select count(*) from organizations) as orgs,
  (select count(*) from auth.users u
     where not exists (select 1 from organization_members m where m.user_id = u.id)
  ) as users_without_an_org;
