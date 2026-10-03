/**
 * Where links in Supabase's emails should send people.
 *
 * Every email this app causes -- sign-up confirmation, resend, password
 * recovery -- lands on `/auth/callback`, which turns the link into a session
 * and then forwards to `next`. Without an explicit target Supabase falls back
 * to the project's Site URL, which has no handler: the account gets confirmed
 * server-side, but the person arrives signed out and reads that as "the link
 * did not work".
 *
 * Supabase only honours a target that is on the project's redirect allow-list
 * (Authentication -> URL Configuration -> Redirect URLs). A target missing
 * from that list is silently replaced by the Site URL, so the list must hold
 * `https://<deployment>/auth/callback` and `http://localhost:3000/auth/callback`.
 *
 * Browser-only: it reads `window.location.origin` so the same build works on
 * localhost, on preview deployments and in production.
 */
export function authCallbackUrl(next = "/"): string {
  const url = new URL("/auth/callback", window.location.origin);
  url.searchParams.set("next", next);
  return url.toString();
}

/**
 * Accept only same-site paths for `next`. `//evil.example` and absolute URLs
 * would turn the callback into an open redirect that runs right after a
 * successful sign-in -- the moment a phishing page is most believable.
 *
 * Resolve, then compare origins. An earlier version checked the string's
 * prefix instead, and `/%09/evil.example` walked straight through it: the
 * URL parser drops tabs and newlines, so the string that passed the check
 * was not the string that was followed. Asking the parser where the URL
 * actually goes is the only check that cannot disagree with the parser.
 */
export function safeNextPath(next: string | null | undefined, origin: string): string {
  if (!next) return "/";
  let resolved: URL;
  try {
    resolved = new URL(next, origin);
  } catch {
    return "/";
  }
  if (resolved.origin !== new URL(origin).origin) return "/";
  return resolved.pathname + resolved.search + resolved.hash;
}
