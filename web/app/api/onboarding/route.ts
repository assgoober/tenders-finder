/**
 * One onboarding turn. Stateless: the browser sends the conversation, the
 * current drafts and any research; nothing here is stored.
 *
 * Runs under the user's own session (RLS applies). The model key never leaves
 * the server.
 */
import { runTurn } from "@/lib/onboarding/agent";
import { chatUnavailableReason, readConfig } from "@/lib/onboarding/config";
import { clock } from "@/lib/onboarding/clock";
import { AgentError, toAgentError } from "@/lib/onboarding/errors";
import { createGemini } from "@/lib/onboarding/gemini";
import { fail, makeRpc, requireMember } from "@/lib/onboarding/http";
import { attachPreviews, previewDraft } from "@/lib/onboarding/preview";
import { TurnRequest } from "@/lib/onboarding/schema";
import { createClient } from "@/lib/supabase/server";

export const dynamic = "force-dynamic";
export const maxDuration = 60;

const TURN_BUDGET_MS = 50_000;
const PREVIEW_MIN_MS = 8_000;

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
    // The platform kills the function at maxDuration; stop the agent early
    // enough to answer with a proper error instead.
    const deadline = clock.now() + TURN_BUDGET_MS;
    const turn = await runTurn(parsed.data, llm, undefined, deadline);
    const drafts =
      deadline - clock.now() > PREVIEW_MIN_MS
        ? await attachPreviews(turn.drafts, parsed.data.drafts, (d) =>
            previewDraft(d.description, d.excludedKeywords, { llm, rpc, config }),
          )
        : turn.drafts.map((d) => ({ ...d, preview: { status: "unavailable" as const } }));
    return Response.json({ ...turn, drafts });
  } catch (e) {
    const err = e instanceof AgentError ? e : toAgentError(e);
    return fail(err.code);
  }
}
