/**
 * One onboarding turn. Stateless: the browser sends the conversation, the
 * current drafts and any research; nothing here is stored.
 *
 * Runs under the user's own session (RLS applies). The model key never leaves
 * the server.
 */
import { runTurn } from "@/lib/onboarding/agent";
import { chatUnavailableReason, readConfig } from "@/lib/onboarding/config";
import { AgentError, toAgentError } from "@/lib/onboarding/errors";
import { createGemini } from "@/lib/onboarding/gemini";
import { fail, makeRpc, requireMember } from "@/lib/onboarding/http";
import { attachPreviews, previewDraft } from "@/lib/onboarding/preview";
import { TurnRequest } from "@/lib/onboarding/schema";
import { createClient } from "@/lib/supabase/server";

export const dynamic = "force-dynamic";
export const maxDuration = 60;

export async function POST(request: Request): Promise<Response> {
  const supabase = await createClient();

  const memberError = await requireMember(supabase);
  if (memberError) return memberError;

  const config = readConfig();
  if (chatUnavailableReason(config)) return fail("unavailable");

  let payload: unknown;
  try {
    payload = await request.json();
  } catch {
    return fail("bad_request");
  }
  const parsed = TurnRequest.safeParse(payload);
  if (!parsed.success) return fail("bad_request");

  const llm = createGemini(config);
  const rpc = makeRpc(supabase);

  try {
    const turn = await runTurn(parsed.data, llm);
    const drafts = await attachPreviews(turn.drafts, parsed.data.drafts, (d) =>
      previewDraft(d.description, d.excludedKeywords, { llm, rpc, config }),
    );
    return Response.json({ ...turn, drafts });
  } catch (e) {
    const err = e instanceof AgentError ? e : toAgentError(e);
    return fail(err.code);
  }
}
