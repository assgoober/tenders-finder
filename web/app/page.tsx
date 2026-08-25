/**
 * Workspace entry point.
 *
 * Reads from mock data today. Wiring live data means replacing these three
 * imports with Supabase queries under the user's own session — RLS scopes
 * them to the caller's organization, so the components below need no change
 * and no organization filter of their own.
 */

import { Workspace } from "@/components/workspace";
import { MOCK_MATCHES, MOCK_PIPELINE, MOCK_TENDERS } from "@/lib/mock-data";

export default function Page() {
  return (
    <main className="mx-auto max-w-[90rem] px-6 py-8">
      <header className="mb-6">
        <h1 className="text-xl font-semibold text-slate-900">Tenders Finder</h1>
        <p className="text-sm text-slate-500">
          Every relevant tender in one place — aggregated, extracted, and matched to your profile.
        </p>
      </header>

      <Workspace tenders={MOCK_TENDERS} matches={MOCK_MATCHES} pipeline={MOCK_PIPELINE} />
    </main>
  );
}
