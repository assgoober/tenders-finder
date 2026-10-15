/** Preview for one draft the user edited by hand. */
import { z } from "zod";

import { chatUnavailableReason, readConfig } from "@/lib/onboarding/config";
import { toAgentError } from "@/lib/onboarding/errors";
import { createGemini } from "@/lib/onboarding/gemini";
import { fail, logFailure, makeRpc, requireMember } from "@/lib/onboarding/http";
import { previewDraft } from "@/lib/onboarding/preview";
import { DraftView } from "@/lib/onboarding/schema";
import { createClient } from "@/lib/supabase/server";

export const dynamic = "force-dynamic";
export const maxDuration = 30;

const Body = z.object({
  description: DraftView.shape.description,
  excludedKeywords: DraftView.shape.excludedKeywords,
});

export async function POST(request: Request): Promise<Response> {
  const supabase = await createClient();

  const memberError = await requireMember(supabase);
  if (memberError) return memberError;

  const config = readConfig();
  if (chatUnavailableReason(config)) return fail("unavailable");

  if (!(request.headers.get("content-type") ?? "").toLowerCase().includes("application/json")) {
    return fail("bad_request");
  }

  let payload: unknown;
  try {
    payload = await request.json();
  } catch {
    return fail("bad_request");
  }
  const parsed = Body.safeParse(payload);
  if (!parsed.success) return fail("bad_request");

  try {
    const preview = await previewDraft(parsed.data.description, parsed.data.excludedKeywords, {
      llm: createGemini(config),
      rpc: makeRpc(supabase),
      config,
    });
    return Response.json({ preview });
  } catch (e) {
    const err = toAgentError(e);
    logFailure("preview", err);
    return fail(err.code);
  }
}
