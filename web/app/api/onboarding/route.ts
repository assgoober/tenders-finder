/**
 * One onboarding turn. Stateless: the browser sends the conversation, the
 * current drafts and any research; nothing here is stored.
 *
 * Runs under the user's own session (RLS applies). The model key never leaves
 * the server.
 */
import { runTurn } from "@/lib/onboarding/agent";
import { chatUnavailableReason, readConfig } from "@/lib/onboarding/config";
import { AgentError, HTTP_STATUS, USER_MESSAGES, toAgentError, type AgentErrorCode } from "@/lib/onboarding/errors";
import { createGemini } from "@/lib/onboarding/gemini";
import { attachPreviews, previewDraft } from "@/lib/onboarding/preview";
import { TurnRequest } from "@/lib/onboarding/schema";
import { createClient } from "@/lib/supabase/server";

export const dynamic = "force-dynamic";
export const maxDuration = 60;

function fail(code: AgentErrorCode): Response {
  return Response.json({ error: { code, message: USER_MESSAGES[code] } }, { status: HTTP_STATUS[code] });
}

export async function POST(request: Request): Promise<Response> {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) return fail("signed_out");

  const { data: orgs } = await supabase.from("organizations").select("id").limit(1);
  if (!orgs || orgs.length === 0) return fail("no_organization");

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
  const rpc = async (fn: string, args: Record<string, unknown>) => {
    const { data, error } = await supabase.rpc(fn, args);
    return { data: data as unknown, error: error ? { message: error.message } : null };
  };

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
