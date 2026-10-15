import { NextResponse } from "next/server";

/**
 * Redirect to a path on this site with a RELATIVE `Location` header.
 *
 * Route handlers used to build an absolute URL from `request.nextUrl.origin`.
 * Behind a hosting proxy (Railway) that origin is the container's own
 * address: the live `/auth/callback` answered with
 * `Location: https://localhost:8080/login?error=link`, so every sign-up
 * confirmation and password-reset link sent people to a page that does not
 * exist. Found on the first deploy, 2026-10-15.
 *
 * A relative `Location` is resolved by the browser against the address it
 * actually used, so it is right on localhost, on Railway and behind any
 * other proxy, without trusting forwarded headers. Cookies set during the
 * request (the new session) are still attached to this response.
 *
 * `path` must already be a same-site path (see `safeNextPath`).
 */
export function redirectToPath(path: string, status: 303 | 307 = 307): NextResponse {
  if (!path.startsWith("/") || path.startsWith("//")) {
    throw new Error(`redirectToPath needs a same-site path, got ${JSON.stringify(path)}`);
  }
  return new NextResponse(null, { status, headers: { Location: path } });
}
