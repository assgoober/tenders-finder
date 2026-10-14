import { createServerClient } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";

import { SUPABASE_ANON_KEY, SUPABASE_URL, isSupabaseConfigured } from "@/lib/supabase/config";

/**
 * Refreshes the Supabase session cookie on every request.
 *
 * Access tokens are short-lived. Without this the session expires while a tab
 * sits open and the next server render returns nothing — which, because every
 * policy in this schema keys on `auth.uid()`, looks exactly like "there are no
 * tenders" rather than "you are logged out". Refreshing here is what keeps
 * those two states distinguishable.
 */
export async function middleware(request: NextRequest) {
  // No project configured: the app runs on fixtures, there is no session to
  // refresh, and constructing a client against an empty URL would throw.
  if (!isSupabaseConfigured) return NextResponse.next();

  let response = NextResponse.next({ request });

  const supabase = createServerClient(SUPABASE_URL, SUPABASE_ANON_KEY, {
    cookies: {
      getAll() {
        return request.cookies.getAll();
      },
      setAll(cookiesToSet) {
        for (const { name, value } of cookiesToSet) {
          request.cookies.set(name, value);
        }
        response = NextResponse.next({ request });
        for (const { name, value, options } of cookiesToSet) {
          response.cookies.set(name, value, options);
        }
      },
    },
  });

  // Do not remove: this call is what performs the refresh. Reading the user
  // (rather than the session) forces validation against the auth server,
  // so a revoked token is caught here instead of being trusted downstream.
  await supabase.auth.getUser();

  return response;
}

export const config = {
  matcher: [
    // Everything except static assets and image optimisation, which carry no
    // session and would only add latency.
    "/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp)$).*)",
  ],
};
