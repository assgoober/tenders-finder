"use client";

/**
 * Request a password-reset email.
 *
 * Before this page existed a customer who forgot their password was locked
 * out for good (HANDOFF, found 18/09): Supabase could issue a recovery link,
 * but the app had nowhere to receive it. The link now goes through
 * `/auth/callback`, which signs the user in, and on to `/reset-password`.
 *
 * The confirmation reads the same whether or not the address has an account.
 * Saying "no such user" would let anyone test which companies are customers,
 * and in a market where the customers compete with each other that list is
 * worth something.
 */

import { useState } from "react";
import Link from "next/link";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { createClient } from "@/lib/supabase/client";
import { isSupabaseConfigured } from "@/lib/supabase/config";
import { authCallbackUrl } from "@/lib/supabase/redirect";

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState(false);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setBusy(true);

    const supabase = createClient();
    const { error: resetError } = await supabase.auth.resetPasswordForEmail(email, {
      redirectTo: authCallbackUrl("/reset-password"),
    });

    setBusy(false);
    // A rate limit is the one failure worth surfacing: the free mailer allows
    // only a few emails an hour, and "check your inbox" for an email that was
    // never sent is the exact waiting game this flow exists to end.
    if (resetError) {
      setError(resetError.message);
      return;
    }
    setSent(true);
  }

  if (!isSupabaseConfigured) {
    return (
      <main className="mx-auto flex min-h-screen max-w-md items-center px-6">
        <Card className="w-full">
          <CardContent className="space-y-2 py-6">
            <h1 className="text-base font-semibold text-slate-900">No project configured</h1>
            <p className="text-sm text-slate-600">
              This build is running on demo data, so there are no passwords to reset.
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
          <h1 className="text-base font-semibold text-slate-900">Reset your password</h1>

          {sent ? (
            <div className="mt-2 space-y-2" role="status">
              <p className="text-sm text-slate-600">
                If <strong>{email}</strong> has an account, a reset link is on its way. Open it in
                this browser.
              </p>
            </div>
          ) : (
            <>
              <p className="mb-5 mt-1 text-sm text-slate-500">
                We will email you a link to choose a new one.
              </p>
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

                {error ? (
                  <p role="alert" className="text-sm text-red-600">
                    {error}
                  </p>
                ) : null}

                <Button type="submit" disabled={busy} className="w-full">
                  {busy ? "Sending…" : "Send reset link"}
                </Button>
              </form>
            </>
          )}

          <p className="mt-4 text-sm text-slate-500">
            <Link href="/login" className="underline">
              Back to sign in
            </Link>
          </p>
        </CardContent>
      </Card>
    </main>
  );
}
