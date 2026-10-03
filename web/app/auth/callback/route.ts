import { NextResponse, type NextRequest } from "next/server";
import type { EmailOtpType } from "@supabase/supabase-js";

import { isSupabaseConfigured } from "@/lib/supabase/config";
import { createClient } from "@/lib/supabase/server";
import { safeNextPath } from "@/lib/supabase/redirect";

/**
 * Landing point for every link in a Supabase auth email: sign-up
 * confirmation and password recovery.
 *
 * Two link shapes arrive here and both are handled:
 *
 * - `?code=` -- the PKCE flow, the default for `@supabase/ssr`. The code is
 *   exchanged against a verifier cookie written when the email was requested,
 *   so it only works in the browser that asked for it. Opening the link on a
 *   phone after signing up on a laptop fails, and the login page says so
 *   rather than showing a bare error.
 * - `?token_hash=&type=` -- what the email templates produce if they are
 *   edited to use `{{ .TokenHash }}`. It needs no verifier, so it works in any
 *   browser. Supported so that changing the templates is enough to fix the
 *   cross-device case; no code change has to follow.
 *
 * Either way the session cookie is written on this response and the user is
 * forwarded to `next` already signed in.
 */
export async function GET(request: NextRequest) {
  const { searchParams, origin } = request.nextUrl;
  const next = safeNextPath(searchParams.get("next"));
  const code = searchParams.get("code");
  const tokenHash = searchParams.get("token_hash");
  const type = searchParams.get("type") as EmailOtpType | null;

  // Supabase reports an expired or already-used link in the query string
  // before we ever get to exchange anything.
  if (searchParams.get("error")) {
    return NextResponse.redirect(new URL("/login?error=link", origin));
  }

  // Demo build: there is no auth server to exchange anything with, and a
  // client built on an empty URL throws.
  if (!isSupabaseConfigured) return NextResponse.redirect(new URL("/", origin));

  const supabase = await createClient();

  if (code) {
    const { error } = await supabase.auth.exchangeCodeForSession(code);
    if (!error) return NextResponse.redirect(new URL(next, origin));
  } else if (tokenHash && type) {
    const { error } = await supabase.auth.verifyOtp({ type, token_hash: tokenHash });
    if (!error) return NextResponse.redirect(new URL(next, origin));
  }

  return NextResponse.redirect(new URL("/login?error=link", origin));
}
