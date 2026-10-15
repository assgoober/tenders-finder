/**
 * One onboarding turn. Stateless: the browser sends the conversation, the
 * current drafts and any research; nothing here is stored.
 *
 * Runs under the user's own session (RLS applies). The model key never leaves
 * the server.
 */
import { runTurn } from "@/lib/onboarding/agent";
import { chatUnavailableReason, readConfig, researchProvider } from "@/lib/onboarding/config";
import { clock } from "@/lib/onboarding/clock";
import { toAgentError } from "@/lib/onboarding/errors";
import { createGemini } from "@/lib/onboarding/gemini";
import { fail, logFailure, makeRpc, requireMember } from "@/lib/onboarding/http";
import { createOpenAIResearcher } from "@/lib/onboarding/openai";
import { attachPreviews, previewDraft } from "@/lib/onboarding/preview";
import { TurnRequest } from "@/lib/onboarding/schema";
import { createClient } from "@/lib/supabase/server";

export const dynamic = "force-dynamic";
export const maxDuration = 60;

const TURN_BUDGET_MS = 50_000;
const PREVIEW_MIN_MS = 8_000;
const MAX_BODY_BYTES = 200_000;

const isJson = (request: Request) =>
  (request.headers.get("content-type") ?? "").toLowerCase().includes("application/json");

export async function POST(request: Request): Promise<Response> {
  const supabase = await createClient();

  const memberError = await requireMember(supabase);
  if (memberError) return memberError;

  const config = readConfig();
  if (chatUnavailableReason(config)) return fail("unavailable");

  // Cheap guard: refuse a huge body before parsing it. runTurn still gives
  // the precise per-message 413.
  const length = Number(request.headers.get("content-length"));
  if (Number.isFinite(length) && length > MAX_BODY_BYTES) return fail("too_long");
  if (!isJson(request)) return fail("bad_request");

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
    // OpenAI researches named companies only when configured (Gemini's
    // free tier refuses Google Search); everything else stays on Gemini.
    const researcher =
      researchProvider(config) === "openai"
        ? createOpenAIResearcher({ apiKey: config.openaiApiKey!, model: config.openaiResearchModel! })
        : undefined;
    const turn = await runTurn(parsed.data, llm, undefined, deadline, researcher);
    // Reusing a preview whose draft did not change costs nothing, so it is
    // kept even when the budget is spent; only NEW previews are skipped then.
    const timeForNew = deadline - clock.now() > PREVIEW_MIN_MS;
    const drafts = await attachPreviews(turn.drafts, parsed.data.drafts, (d) =>
      timeForNew
        ? previewDraft(d.description, d.excludedKeywords, { llm, rpc, config })
        : Promise.resolve({ status: "unavailable" as const }),
    );
    return Response.json({ ...turn, drafts });
  } catch (e) {
    const err = toAgentError(e);
    logFailure("turn", err);
    return fail(err.code);
  }
}
