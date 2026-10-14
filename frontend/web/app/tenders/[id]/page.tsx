/**
 * Deep-linkable tender page.
 *
 * The side panel is the primary way to read a tender, but an alert email or a
 * message to a colleague needs a URL that opens straight to one — so the same
 * extraction is addressable on its own route.
 *
 * `generateStaticParams` is gone. Prerendering these would mean baking a fixed
 * set of ids into the build, and tender ids arrive from the scraper after it.
 * The route reads the session cookie, so it is per-request regardless.
 *
 * A tender that does not exist and one that RLS hides both return null, and
 * both render 404 — deliberately. Distinguishing them would confirm to an
 * unauthorised caller that a given id is real.
 */

import Link from "next/link";
import { notFound, redirect } from "next/navigation";

import { TenderDetail } from "@/components/tender-detail";
import { fetchTender } from "@/lib/api";
import { MOCK_TENDERS } from "@/lib/mock-data";
import { isSupabaseConfigured } from "@/lib/supabase/config";
import { createClient } from "@/lib/supabase/server";
import type { Tender } from "@/lib/types";

export default async function TenderPage({ params }: { params: { id: string } }) {
  let tender: Tender | null;

  if (!isSupabaseConfigured) {
    tender = MOCK_TENDERS.find((entry) => entry.id === params.id) ?? null;
  } else {
    const supabase = await createClient();
    const {
      data: { user },
    } = await supabase.auth.getUser();
    if (!user) redirect("/login");

    tender = await fetchTender(params.id);
  }

  if (!tender) notFound();

  return (
    <main className="mx-auto max-w-4xl px-6 py-8">
      <Link href="/" className="text-sm text-sky-700 underline-offset-4 hover:underline">
        ← Back to workspace
      </Link>
      <div className="mt-4">
        <TenderDetail tender={tender} />
      </div>
    </main>
  );
}
