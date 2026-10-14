-- 0008_revoke_public_execute.sql
-- Finish what 0007 only appeared to do.
--
-- 0007 ran `revoke execute on function generate_matches_for_tender(uuid)
-- from anon, authenticated`. It succeeded, and afterwards the function's ACL
-- read:
--
--     =X/postgres  |  postgres=X/postgres  |  service_role=X/postgres
--
-- `anon` and `authenticated` are absent from that list, which reads as
-- closed. It is not. The leading `=X/postgres` is the grant to PUBLIC that
-- PostgreSQL attaches to every newly created function, and every role
-- inherits PUBLIC. Revoking a role by name removes only its explicit entry
-- and leaves the wide grant untouched, so:
--
--     has_function_privilege('anon', 'public.generate_matches_for_tender(uuid)', 'EXECUTE')
--     -> true
--
-- still, after 0007. The ACL was the wrong thing to read. The effective
-- privilege is the thing that answers the question, and the two disagree
-- exactly where it matters.
--
-- Scope is deliberately one function. `auth_org_ids()` and
-- `is_org_member(uuid)` are also SECURITY DEFINER and also PUBLIC-executable,
-- and they are NOT touched here: both are called from inside the RLS policy
-- expressions in 0002 and 0004, those calls run as the querying user, and a
-- privilege check applies to them. Revoking EXECUTE from `authenticated`
-- there would not harden the policies — it would stop them evaluating, and
-- take every tenant-scoped read down with them.
--
-- The trigger functions (`on_tender_embedded`, `set_updated_at`) are left
-- alone for a different reason: they return `trigger`, so PostgREST will not
-- expose them and a caller cannot invoke them over the API at all.

revoke execute on function public.generate_matches_for_tender(uuid) from public;

-- `service_role` already holds an explicit grant and keeps it — it is the
-- caller that legitimately backfills matches. Stated here rather than
-- assumed, so a future reader does not have to check whether removing the
-- PUBLIC grant took the pipeline's own access with it.
grant execute on function public.generate_matches_for_tender(uuid) to service_role;

-- The trigger path is unaffected. `on_tender_embedded()` is SECURITY DEFINER
-- and owned by postgres, so its internal call to this function is checked
-- against postgres, which retains EXECUTE.

-- Verify with the effective privilege, not the ACL:
--
--   select has_function_privilege('anon','public.generate_matches_for_tender(uuid)','EXECUTE');
--   -- expected: false
