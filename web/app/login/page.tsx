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
 * There is no sign-up form on purpose. Access to a tender workspace is granted
 * by whoever runs it, not claimed by whoever finds the URL.
 */

import { useState } from "react";
import { useRouter } from "next/navigation";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { createClient } from "@/lib/supabase/client";
import { isSupabaseConfigured } from "@/lib/supabase/config";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setBusy(true);

    const supabase = createClient();
    const { error: signInError } = await supabase.auth.signInWithPassword({ email, password });

    if (signInError) {
      setError(signInError.message);
      setBusy(false);
      return;
    }

    // refresh() re-runs the server component with the new session cookie.
    // push() alone would render the workspace from the cached, signed-out RSC
    // payload — an empty dashboard immediately after a successful login.
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

            <Button type="submit" disabled={busy} className="w-full">
              {busy ? "Signing in…" : "Sign in"}
            </Button>
          </form>
        </CardContent>
      </Card>
    </main>
  );
}
