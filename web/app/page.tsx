/**
 * Workspace entry point.
 *
 * Reads live data through the signed-in user's own session, so RLS scopes
 * every query to their organization and the components below need no
 * organization filter of their own.
 *
 * Four states, kept distinct on purpose. Three of them render an empty or
 * partial workspace, and if they were not told apart the screen would look the
 * same in each case — leaving "is this broken?" as the only reading:
 *
 *   1. No project configured  → demo fixtures, labelled as invented.
 *   2. Configured, signed out → /login. Every policy keys on `auth.uid()`,
 *      so a signed-out session reads as an empty database, not as an error.
 *   3. Signed in, no organization → tenders are visible (that policy grants
 *      to `authenticated` outright) but alerts and pipeline are empty by
 *      definition, because both key on organization membership.
 *   4. Signed in, no tenders yet → the scraper has not written to this
 *      project. Nothing is wrong; nothing has run.
 */

import { cookies } from "next/headers";
import Link from "next/link";
import { redirect } from "next/navigation";

import { DemoNotice } from "@/components/demo-notice";
import { Workspace } from "@/components/workspace";
import { fetchWorkspace } from "@/lib/api";
import { MOCK_MATCHES, MOCK_PIPELINE, MOCK_TENDERS } from "@/lib/mock-data";
import { SKIP_COOKIE, shouldOnboard } from "@/lib/onboarding/gate";
import { isSupabaseConfigured } from "@/lib/supabase/config";
import { createClient } from "@/lib/supabase/server";
import { daysUntil } from "@/lib/utils";

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <main className="mx-auto max-w-[90rem] px-6 py-8">
      <header className="mb-6 flex items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold text-slate-900">Tenders Finder</h1>
          <p className="text-sm text-slate-500">
            Every relevant tender in one place — aggregated, extracted, and matched to your profile.
          </p>
        </div>
        {isSupabaseConfigured ? (
          <div className="flex items-center gap-2">
            <Link
              href="/profiles"
              className="rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-50"
            >
              Alert profiles
            </Link>
            <form action="/auth/signout" method="post">
              <button
                type="submit"
                className="rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-50"
              >
                Sign out
              </button>
            </form>
          </div>
        ) : null}
      </header>
      {children}
    </main>
  );
}

function Notice({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div
      role="status"
      className="mb-6 rounded-md border border-slate-300 bg-slate-50 px-4 py-3 text-sm text-slate-700"
    >
      <span className="font-semibold">{title}</span> {children}
    </div>
  );
}

export default async function Page({ searchParams }: { searchParams: { onboarded?: string } }) {
  // State 1 — no project. Render fixtures rather than crash, and say so.
  if (!isSupabaseConfigured) {
    return (
      <Shell>
        <DemoNotice />
        <Workspace
          tenders={MOCK_TENDERS}
          matches={MOCK_MATCHES}
          pipeline={MOCK_PIPELINE}
          // Fixtures are the whole corpus in demo mode, so counting them is
          // not the shortcut it would be against a real database.
          counts={{
            open: MOCK_TENDERS.filter(
              (t) => t.status === "open" || t.status === "closing_soon",
            ).length,
            closingWithinSevenDays: MOCK_TENDERS.filter((t) => {
              const d = daysUntil(t.deadline);
              return d !== null && d >= 0 && d <= 7;
            }).length,
          }}
        />
      </Shell>
    );
  }

  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();

  // State 2 — signed out.
  if (!user) redirect("/login");

  // RLS makes this the honest test for membership: the policy on
  // `organizations` is `is_org_member(id)`, so a user in no organization gets
  // zero rows rather than an error.
  const { data: orgs } = await supabase.from("organizations").select("id").limit(1);
  const hasOrganization = (orgs?.length ?? 0) > 0;

  // A member with no alert profile has nothing to be alerted about: send them
  // to the onboarding chat first — unless they chose to skip it.
  if (hasOrganization) {
    const { data: anyProfile, error: profileError } = await supabase
      .from("alert_profiles")
      .select("id")
      .limit(1);
    const skipped = (await cookies()).get(SKIP_COOKIE)?.value === "1";
    // A failed probe says nothing about whether profiles exist: never send an
    // existing customer to onboarding because a query errored.
    if (
      !profileError &&
      shouldOnboard({ hasOrganization, hasProfile: (anyProfile?.length ?? 0) > 0, skipped })
    ) {
      redirect("/onboarding");
    }
  }

  const { tenders, matches, pipeline, counts } = await fetchWorkspace();

  return (
    <Shell>
      {searchParams.onboarded === "1" ? (
        <Notice title="Your alert profiles are saved.">
          Matching starts with tonight&apos;s run — new tenders that fit will appear here and in
          your email. You can change your profiles any time under Alert profiles.
        </Notice>
      ) : null}

      {/* State 3 — signed in, but in no organization. */}
      {!hasOrganization ? (
        <Notice title="You are not a member of any organization.">
          Tenders are visible to any signed-in user, but alerts and the bid pipeline are scoped to an
          organization, so both stay empty until your account is added to one. The two inserts that
          do it are in <code>supabase/README.md</code>.
        </Notice>
      ) : null}

      {/* State 4 — nothing has been scraped into this project yet. */}
      {hasOrganization && tenders.length === 0 ? (
        <Notice title="No tenders yet.">
          The database is reachable and empty — the scraper has not written to this project. Run{" "}
          <code>POST /ingest</code> against the service to populate it.
        </Notice>
      ) : null}

      <Workspace tenders={tenders} matches={matches} pipeline={pipeline} counts={counts} />
    </Shell>
  );
}
