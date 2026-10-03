import type { createClient } from "@/lib/supabase/server";

/**
 * The caller's organization, read through their own session.
 *
 * RLS on `organizations` is `is_org_member(id)`, so this returns only
 * organizations the user actually belongs to — the filter is the policy, not
 * a where clause someone has to remember to write.
 *
 * A user in two organizations is refused rather than resolved arbitrarily.
 * There is no organization switcher in this UI yet, so picking the first row
 * would file a tender into a pipeline — or an alert profile into an account —
 * the user did not choose, and in a product whose whole premise is that
 * competing contractors share a database, guessing is the wrong failure mode.
 *
 * Lives outside `lib/actions/` on purpose: every export of a "use server"
 * file is callable from the browser, and this is a helper, not an action.
 */
export async function resolveOrganization(
  supabase: Awaited<ReturnType<typeof createClient>>,
): Promise<{ id: string } | { error: string }> {
  const { data, error } = await supabase.from("organizations").select("id").limit(2);

  if (error) return { error: error.message };
  if (!data || data.length === 0) {
    return { error: "Your account is not a member of an organization yet." };
  }
  if (data.length > 1) {
    return {
      error:
        "Your account belongs to more than one organization and this screen cannot yet ask which one to use.",
    };
  }
  return { id: (data[0] as { id: string }).id };
}
