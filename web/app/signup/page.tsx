"use client";

/**
 * Sign up.
 *
 * This reverses a decision the login page documented: "There is no sign-up
 * form on purpose. Access to a tender workspace is granted by whoever runs
 * it." That was right while the product was operated for one organisation by
 * hand. It is wrong for a product sold to many, and the reversal is written
 * down here rather than left as a contradiction between two files.
 *
 * The organisation is NOT created here. Migration 0017 provisions it in a
 * trigger on `auth.users`, in the same transaction as the account, because
 * this page can only create it in a second request -- and a closed tab
 * between the two leaves an account that can log in and see nothing, since
 * every RLS policy in the schema resolves through organisation membership.
 *
 * The company name is passed as user metadata for that trigger to read.
 */

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { createClient } from "@/lib/supabase/client";
import { isSupabaseConfigured } from "@/lib/supabase/config";

export default function SignUpPage() {
  const router = useRouter();
  const [company, setCompany] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [confirmationSent, setConfirmationSent] = useState(false);
  const [busy, setBusy] = useState(false);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setBusy(true);

    const supabase = createClient();
    const { data, error: signUpError } = await supabase.auth.signUp({
      email,
      password,
      options: { data: { company_name: company.trim() } },
    });

    if (signUpError) {
      setError(signUpError.message);
      setBusy(false);
      return;
    }

    // Whether a session comes back depends on a project setting this page
    // does not control. With email confirmation on, `session` is null and the
    // account is real but unusable until the link is clicked -- saying so is
    // the difference between "nothing happened" and "check your email".
    if (!data.session) {
      setConfirmationSent(true);
      setBusy(false);
      return;
    }

    // refresh() re-runs the server component with the new session cookie.
    // push() alone would render the workspace from the cached, signed-out RSC
    // payload -- an empty dashboard immediately after a successful sign-up.
    router.push("/");
    router.refresh();
  }

  if (!isSupabaseConfigured) {
    return (
      <main className="mx-auto flex min-h-screen max-w-md items-center px-6">
        <Card className="w-full">
          <CardContent className="space-y-2 py-6">
            <h1 className="text-base font-semibold text-slate-900">No project configured</h1>
            <p className="text-sm text-slate-600">
              This build has no <code>NEXT_PUBLIC_SUPABASE_URL</code>, so there is no account
              system to sign up to. It is running on demo data.
            </p>
          </CardContent>
        </Card>
      </main>
    );
  }

  if (confirmationSent) {
    return (
      <main className="mx-auto flex min-h-screen max-w-md items-center px-6">
        <Card className="w-full">
          <CardContent className="space-y-2 py-6">
            <h1 className="text-base font-semibold text-slate-900">Confirm your email</h1>
            <p className="text-sm text-slate-600">
              We sent a confirmation link to <strong>{email}</strong>. Your workspace is ready as
              soon as you open it.
            </p>
            <p className="text-sm text-slate-500">
              <Link href="/login" className="underline">
                Back to sign in
              </Link>
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
          <p className="mb-5 mt-1 text-sm text-slate-500">
            Create a workspace for your company.
          </p>

          <form onSubmit={onSubmit} className="space-y-4">
            <div className="space-y-1.5">
              <label htmlFor="company" className="block text-sm font-medium text-slate-700">
                Company
              </label>
              <input
                id="company"
                type="text"
                required
                autoComplete="organization"
                value={company}
                onChange={(e) => setCompany(e.target.value)}
                className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm outline-none focus:border-slate-900 focus:ring-1 focus:ring-slate-900"
              />
            </div>

            <div className="space-y-1.5">
              <label htmlFor="email" className="block text-sm font-medium text-slate-700">
                Work email
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
                minLength={8}
                autoComplete="new-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm outline-none focus:border-slate-900 focus:ring-1 focus:ring-slate-900"
              />
              <p className="text-xs text-slate-500">At least 8 characters.</p>
            </div>

            {error ? (
              // Announced, not just displayed: the message appears after the
              // button is pressed, which a screen reader would otherwise miss.
              <p role="alert" className="text-sm text-red-600">
                {error}
              </p>
            ) : null}

            <Button type="submit" disabled={busy} className="w-full">
              {busy ? "Creating your workspace…" : "Create workspace"}
            </Button>
          </form>

          <p className="mt-4 text-sm text-slate-500">
            Already have an account?{" "}
            <Link href="/login" className="underline">
              Sign in
            </Link>
          </p>
        </CardContent>
      </Card>
    </main>
  );
}
