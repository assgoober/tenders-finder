import { redirectToPath } from "@/lib/http/relative-redirect";
import { createClient } from "@/lib/supabase/server";

/**
 * Sign out. POST only — a GET would let any image or link on a page log the
 * user out by being loaded.
 */
export async function POST() {
  const supabase = await createClient();
  await supabase.auth.signOut();
  return redirectToPath("/login", 303);
}
