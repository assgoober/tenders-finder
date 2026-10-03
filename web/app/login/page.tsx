"use client";

/**
 * Sign in.
 *
 * Email and password rather than a magic link. The free Supabase tier's
 * built-in mailer allows only a few messages an hour, which turns a magic-link
 * flow into a waiting game exactly when someone is trying it for the first
 * time. Create users under Authentication → Users in the Supabase dashboard;
 * that path also skips email confirmation, so an account works immediately.
 *
 * Sign-up used to be deliberately absent: access was granted by whoever ran
 * the workspace, not claimed by whoever found the URL. That held while the
 * product was operated for one organisation by hand. `/signup` reverses it,
 * and the organisation is provisioned by a trigger rather than by that page
 * (migration 0017) -- see the note there for why it cannot be done in the
 * browser.
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { createClient } from "@/lib/supabase/client";
import { isSupabaseConfigured } from "@/lib/supabase/config";
import { authCallbackUrl } from "@/lib/supabase/redirect";

/** Set by `/auth/callback` when an email link could not be turned into a session. */
const LINK_ERROR =
  "That link has expired, was already used, or was opened in a different browser from the one " +
  "that requested it. If you were confirming your email, try signing in -- the account may " +
  "already be confirmed.";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [unconfirmed, setUnconfirmed] = useState(false);
  const [resent, setResent] = useState(false);
  const [busy, setBusy] = useState(false);

  // Read in an effect rather than with useSearchParams, which would force a
  // Suspense boundary around the whole page in Next 14.
  useEffect(() => {
    if (new URLSearchParams(window.location.search).get("error") === "link") {
      setError(LINK_ERROR);
    }
  }, []);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setUnconfirmed(false);
    setResent(false);
    setBusy(true);

    const supabase = createClient();
    const { error: signInError } = await supabase.auth.signInWithPassword({ email, password });

    if (signInError) {
      // Email confirmation is on in the live project. Until the link is
      // clicked, a correct password is refused -- and with no way to get a
      // second email, an account whose first email was lost is stuck.
      if (signInError.code === "email_not_confirmed") {
        setUnconfirmed(true);
        setError("This email address has not been confirmed yet. Open the link we emailed you.");
      } else {
        setError(signInError.message);
      }
      setBusy(false);
      return;
    }

    // refresh() re-runs the server component with the new session cookie.
    // push() alone would render the workspace from the cached, signed-out RSC
    // payload — an empty dashboard immediately after a successful login.
    router.push("/");
    router.refresh();
  }

  async function resendConfirmation() {
    setBusy(true);
    const { error: resendError } = await createClient().auth.resend({
      type: "signup",
      email,
      options: { emailRedirectTo: authCallbackUrl() },
    });
    setBusy(false);
    if (resendError) {
      // Most often the free mailer's hourly limit; say so rather than pretend.
      setError(resendError.message);
      return;
    }
    setResent(true);
    setError(null);
  }

  if (!isSupabaseConfigured) {
    return (
      <main className="mx-auto flex min-h-screen max-w-md items-center px-6">
        <Card className="w-full">
          <CardContent className="space-y-2 py-6">
            <h1 className="text-base font-semibold text-slate-900">No project configured</h1>
            <p className="text-sm text-slate-600">
              This build has no <code>NEXT_PUBLIC_SUPABASE_URL</code>, so there is nothing to sign
              in to. It is running on demo data — go back to the workspace to see it.
            </p>
          </CardContent>
        </Card>
      </main>
    );
  }

  return (
    <main className="mx-auto flex min-h-screen max-w-md items-center px-6">
      <Card className="w-full">
        <CardContent className="py-6">
          <h1 className="text-base font-semibold text-slate-900">Tenders Finder</h1>
          <p className="mb-5 mt-1 text-sm text-slate-500">Sign in to your workspace.</p>

          <form onSubmit={onSubmit} className="space-y-4">
            <div className="space-y-1.5">
              <label htmlFor="email" className="block text-sm font-medium text-slate-700">
                Email
              </label>
              <input
                id="email"
                type="email"
                required
                autoComplete="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm outline-none focus:border-slate-900 focus:ring-1 focus:ring-slate-900"
              />
            </div>

            <div className="space-y-1.5">
              <label htmlFor="password" className="block text-sm font-medium text-slate-700">
                Password
              </label>
              <input
                id="password"
                type="password"
                required
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm outline-none focus:border-slate-900 focus:ring-1 focus:ring-slate-900"
              />
            </div>

            {error ? (
              // Announced, not just displayed: the message appears after the
              // button is pressed, which a screen reader would otherwise miss.
              <p role="alert" className="text-sm text-red-600">
                {error}
              </p>
            ) : null}

            {unconfirmed ? (
              <Button
                type="button"
                variant="outline"
                disabled={busy}
                onClick={resendConfirmation}
                className="w-full"
              >
                Send the confirmation email again
              </Button>
            ) : null}

            {resent ? (
              <p role="status" className="text-sm text-slate-600">
                Sent to <strong>{email}</strong>. Open it in this browser.
              </p>
            ) : null}

            <Button type="submit" disabled={busy} className="w-full">
              {busy ? "Signing in…" : "Sign in"}
            </Button>
          </form>

          <p className="mt-4 text-sm text-slate-500">
            <Link href="/forgot-password" className="underline">
              Forgot your password?
            </Link>
          </p>

          <p className="mt-2 text-sm text-slate-500">
            No account yet?{" "}
            <Link href="/signup" className="underline">
              Create a workspace
            </Link>
          </p>
        </CardContent>
      </Card>
    </main>
  );
}
