import { cookies } from "next/headers";
import { createServerClient } from "@supabase/ssr";

import { SUPABASE_ANON_KEY, SUPABASE_URL } from "@/lib/supabase/config";

/**
 * Supabase client for server components and route handlers.
 *
 * Uses the **anon** key and the caller's own session cookie, never the
 * service-role key. That is the whole security model of this dashboard: every
 * query runs as the signed-in user, so row level security decides what comes
 * back, and no component has to remember to filter by organization. A
 * service-role client here would bypass RLS and quietly turn every query into
 * a cross-tenant read — defence contractors competing for the same tenders is
 * exactly the case this must not get wrong.
 *
 * `cookies()` is dynamic, so any route that calls this renders per-request
 * rather than being statically prerendered. That is correct for a workspace
 * whose contents differ per viewer.
 */
export async function createClient() {
  const cookieStore = await cookies();

  return createServerClient(SUPABASE_URL, SUPABASE_ANON_KEY, {
    cookies: {
      getAll() {
        return cookieStore.getAll();
      },
      setAll(cookiesToSet) {
        try {
          for (const { name, value, options } of cookiesToSet) {
            cookieStore.set(name, value, options);
          }
        } catch {
          // Called from a Server Component, where cookies are read-only.
          // The middleware refreshes the session on every request, so the
          // write this drops has already happened there. Swallowing it here
          // is the documented pattern, not an oversight.
        }
      },
    },
  });
}
