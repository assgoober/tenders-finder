/**
 * Alert profiles: what this organization wants to be told about.
 *
 * Read through the user's own session like every other page, so RLS
 * (`alert_profiles_rw`) scopes the list to their organization. Writes go
 * through `lib/actions/profiles.ts`.
 *
 * Each profile shows whether the nightly sweep has embedded it yet. Without
 * that, a profile saved at 14:00 sits with no matches until the next morning
 * and reads as "this does not work" -- the same misreading the home page's
 * four states exist to prevent.
 */

import Link from "next/link";
import { redirect } from "next/navigation";

import { ProfileEditor, type ProfileView } from "@/components/profile-editor";
import { isSupabaseConfigured } from "@/lib/supabase/config";
import { createClient } from "@/lib/supabase/server";

type Row = {
  id: string;
  name: string;
  description: string;
  keywords: string[] | null;
  excluded_keywords: string[] | null;
  notify_email: boolean;
  is_active: boolean;
  embedding: unknown;
  tender_matches: { count: number }[] | null;
};

export default async function ProfilesPage() {
  if (!isSupabaseConfigured) redirect("/");

  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login");

  const { data: orgs } = await supabase.from("organizations").select("id").limit(1);
  const hasOrganization = (orgs?.length ?? 0) > 0;

  // `embedding` is fetched only to ask whether it is null. A handful of
  // profiles per organization makes that cheaper than a view to compute it.
  const { data, error } = await supabase
    .from("alert_profiles")
    .select(
      "id, name, description, keywords, excluded_keywords, notify_email, is_active, embedding, tender_matches(count)",
    )
    .order("created_at", { ascending: true });

  const profiles: ProfileView[] = ((data ?? []) as Row[]).map((row) => ({
    id: row.id,
    name: row.name,
    description: row.description,
    keywords: row.keywords ?? [],
    excludedKeywords: row.excluded_keywords ?? [],
    notifyEmail: row.notify_email,
    isActive: row.is_active,
    embedded: row.embedding !== null,
    matchCount: row.tender_matches?.[0]?.count ?? 0,
  }));

  return (
    <main className="mx-auto max-w-3xl px-6 py-8">
      <p className="mb-4 text-sm">
        <Link href="/" className="text-slate-500 underline">
          ← Back to the workspace
        </Link>
      </p>
      <h1 className="text-xl font-semibold text-slate-900">Alert profiles</h1>
      <p className="mb-6 mt-1 text-sm text-slate-500">
        Describe what your company builds and bids on. Every new tender is compared against this
        description, and the ones that fit are sent to you by email and shown in your alerts.
      </p>

      {error ? (
        <p role="alert" className="mb-6 text-sm text-red-600">
          Could not load your profiles: {error.message}
        </p>
      ) : null}

      {!hasOrganization ? (
        <p role="status" className="text-sm text-slate-600">
          Your account is not a member of an organization yet, so there is nowhere to save a
          profile.
        </p>
      ) : (
        <ProfileEditor profiles={profiles} />
      )}
    </main>
  );
}
