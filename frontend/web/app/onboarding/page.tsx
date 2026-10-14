/**
 * Onboarding: tell us your company, or what you want, in your own words.
 * Requires sign-in and an organization (there is nowhere else to save).
 */
import { redirect } from "next/navigation";

import { OnboardingChat } from "@/components/onboarding-chat";
import { chatUnavailableReason, readConfig } from "@/lib/onboarding/config";
import { isSupabaseConfigured } from "@/lib/supabase/config";
import { createClient } from "@/lib/supabase/server";

export const dynamic = "force-dynamic";

export default async function OnboardingPage() {
  if (!isSupabaseConfigured) redirect("/");

  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login");

  const { data: orgs } = await supabase.from("organizations").select("id").limit(1);
  if (!orgs || orgs.length === 0) redirect("/profiles");

  return (
    <main className="mx-auto max-w-3xl px-6 py-8">
      <h1 className="text-xl font-semibold text-slate-900">Set up your tender alerts</h1>
      <p className="mb-6 mt-1 text-sm text-slate-500">
        Tell me which company you work for, or describe the tenders you want. I&apos;ll draft your
        alert profiles — nothing is saved until you approve it.
      </p>
      <OnboardingChat unavailable={chatUnavailableReason(readConfig()) !== null} />
    </main>
  );
}
