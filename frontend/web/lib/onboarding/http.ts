/**
 * Shared HTTP utilities for onboarding API routes.
 * Server-only helpers for authentication, authorization, and error handling.
 */
import { HTTP_STATUS, USER_MESSAGES, type AgentError, type AgentErrorCode } from "./errors";
import type { RpcFn } from "./preview";
import type { createClient } from "@/lib/supabase/server";

export function fail(code: AgentErrorCode): Response {
  return Response.json({ error: { code, message: USER_MESSAGES[code] } }, { status: HTTP_STATUS[code] });
}

/**
 * One line in the server log for every failed turn. Without it the first live
 * failure (Google 503 overload, 2026-10-15) left the Railway logs empty and
 * had to be reproduced on a laptop. Code plus the provider's own message,
 * capped; never the conversation, which is the user's.
 */
export function logFailure(route: string, err: AgentError): void {
  console.error(`[onboarding:${route}] ${err.code}: ${err.message.slice(0, 300)}`);
}

export async function requireMember(
  supabase: Awaited<ReturnType<typeof createClient>>,
): Promise<Response | null> {
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) return fail("signed_out");

  const { data: orgs, error } = await supabase.from("organizations").select("id").limit(1);
  if (error) return fail("upstream");
  if (!orgs || orgs.length === 0) return fail("no_organization");

  return null;
}

export function makeRpc(supabase: Awaited<ReturnType<typeof createClient>>): RpcFn {
  return async (fn: string, args: Record<string, unknown>) => {
    const { data, error } = await supabase.rpc(fn, args);
    return { data: data as unknown, error: error ? { message: error.message } : null };
  };
}
