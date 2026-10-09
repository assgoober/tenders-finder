/** Preview for one draft the user edited by hand. */
import { z } from "zod";

import { chatUnavailableReason, readConfig } from "@/lib/onboarding/config";
import { HTTP_STATUS, USER_MESSAGES, type AgentErrorCode } from "@/lib/onboarding/errors";
import { createGemini } from "@/lib/onboarding/gemini";
import { previewDraft } from "@/lib/onboarding/preview";
import { DraftView } from "@/lib/onboarding/schema";
import { createClient } from "@/lib/supabase/server";

export const dynamic = "force-dynamic";
export const maxDuration = 30;

const Body = z.object({
  description: DraftView.shape.description,
  excludedKeywords: DraftView.shape.excludedKeywords,
});

function fail(code: AgentErrorCode): Response {
  return Response.json({ error: { code, message: USER_MESSAGES[code] } }, { status: HTTP_STATUS[code] });
}

export async function POST(request: Request): Promise<Response> {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) return fail("signed_out");

  const config = readConfig();
  if (chatUnavailableReason(config)) return fail("unavailable");

  let payload: unknown;
  try {
    payload = await request.json();
  } catch {
    return fail("bad_request");
  }
  const parsed = Body.safeParse(payload);
  if (!parsed.success) return fail("bad_request");

  const rpc = async (fn: string, args: Record<string, unknown>) => {
    const { data, error } = await supabase.rpc(fn, args);
    return { data: data as unknown, error: error ? { message: error.message } : null };
  };
  const preview = await previewDraft(parsed.data.description, parsed.data.excludedKeywords, {
    llm: createGemini(config),
    rpc,
    config,
  });
  return Response.json({ preview });
}
