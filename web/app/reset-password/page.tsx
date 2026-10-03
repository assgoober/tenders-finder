"use client";

/**
 * Choose a new password.
 *
 * Reached from a recovery email via `/auth/callback`, which has already
 * signed the user in -- so this is `updateUser` on the current session, not a
 * second token check. A visitor who arrives without a session (expired link,
 * link opened in another browser, or typed the URL) is told to request a new
 * link instead of being shown a form that can only fail.
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { createClient } from "@/lib/supabase/client";
import { isSupabaseConfigured } from "@/lib/supabase/config";

type SessionState = "checking" | "ready" | "missing";

export default function ResetPasswordPage() {
  const router = useRouter();
  const [session, setSession] = useState<SessionState>("checking");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!isSupabaseConfigured) return;
    createClient()
      .auth.getUser()
      .then(({ data }) => setSession(data.user ? "ready" : "missing"));
  }, []);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);

    if (password !== confirm) {
      setError("The two passwords do not match.");
      return;
    }

    setBusy(true);
    const { error: updateError } = await createClient().auth.updateUser({ password });
    if (updateError) {
      setError(updateError.message);
      setBusy(false);
      return;
    }

    // Same reason as the login page: refresh() so the workspace renders with
    // the session rather than from a cached signed-out payload.
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
          <h1 className="text-base font-semibold text-slate-900">Choose a new password</h1>

          {session === "checking" ? (
            <p className="mt-2 text-sm text-slate-500">Checking your link…</p>
          ) : session === "missing" ? (
            <div className="mt-2 space-y-2" role="status">
              <p className="text-sm text-slate-600">
                This reset link has expired or was opened in a different browser from the one that
                requested it.
              </p>
              <p className="text-sm text-slate-500">
                <Link href="/forgot-password" className="underline">
                  Request a new link
                </Link>
              </p>
            </div>
          ) : (
            <form onSubmit={onSubmit} className="mt-5 space-y-4">
              <div className="space-y-1.5">
                <label htmlFor="password" className="block text-sm font-medium text-slate-700">
                  New password
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

              <div className="space-y-1.5">
                <label htmlFor="confirm" className="block text-sm font-medium text-slate-700">
                  Repeat new password
                </label>
                <input
                  id="confirm"
                  type="password"
                  required
                  minLength={8}
                  autoComplete="new-password"
                  value={confirm}
                  onChange={(e) => setConfirm(e.target.value)}
                  className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm outline-none focus:border-slate-900 focus:ring-1 focus:ring-slate-900"
                />
              </div>

              {error ? (
                <p role="alert" className="text-sm text-red-600">
                  {error}
                </p>
              ) : null}

              <Button type="submit" disabled={busy} className="w-full">
                {busy ? "Saving…" : "Save new password"}
              </Button>
            </form>
          )}
        </CardContent>
      </Card>
    </main>
  );
}
