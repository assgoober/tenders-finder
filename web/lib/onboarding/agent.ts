/**
 * One turn of the onboarding conversation.
 *
 * classify → (research, only for a newly named company) → structure.
 * Research and structuring are separate calls on purpose: the research call
 * has Google Search and returns the pages it used as metadata; the
 * structuring call has no tools and may cite those pages only by number.
 * Links therefore come from Google, never from model text.
 */
import { z } from "zod";

import { AgentError, USER_MESSAGES } from "./errors";
import type { GeminiLike } from "./llm";
import {
  BLOCKED_TEXT_HE,
  CLASSIFY_SYSTEM,
  DRAFT_SYSTEM,
  GEO_NOTICE_TEXT,
  GEO_NOTICE_TEXT_HE,
  RESEARCH_SYSTEM,
  classifyPrompt,
  draftPrompt,
  researchPrompt,
} from "./prompts";
import {
  Classification,
  MAX_MESSAGE_CHARS,
  MAX_USER_MESSAGES,
  ModelTurn,
  sanitizeModelTurn,
  type Candidate,
  type DraftView,
  type Research,
  type TurnRequest,
} from "./schema";
import { numberSources, toDraftView } from "./sources";

export { PROMPT_VERSION } from "./prompts";
export const GEO_NOTICE = GEO_NOTICE_TEXT;

export type TurnResult = {
  kind: "drafts" | "candidates" | "not_found" | "clarify";
  reply: string;
  candidates: Candidate[];
  drafts: DraftView[];
  research: Research | null;
};

function jsonSchemaFor(schema: z.ZodType): Record<string, unknown> {
  const out = { ...(z.toJSONSchema(schema, { unrepresentable: "any" }) as Record<string, unknown>) };
  delete out.$schema;
  return out;
}

const RETRY_MIN_MS = 15_000;
const CALL_MIN_MS = 10_000;

/** The adapter's default per-call timeout (gemini.ts); never ask for more. */
const MAX_CALL_MS = 45_000;
/** Kept back from each call so the route can still answer before its limit. */
const CALL_MARGIN_MS = 2_000;

function timeLeft(deadline: number | undefined): number {
  return deadline === undefined ? Infinity : deadline - Date.now();
}

/**
 * How long one model call may take. Without this a call that starts with
 * 15 s left still gets the adapter's full 45 s and can outlive the route's
 * 60 s limit — the platform then kills the function mid-call.
 */
function callTimeout(deadline: number | undefined): number | undefined {
  if (deadline === undefined) return undefined;
  return Math.max(1_000, Math.min(MAX_CALL_MS, timeLeft(deadline) - CALL_MARGIN_MS));
}

/** Ask for JSON, validate it, retry once with the problem fed back. */
export async function structured<T>(
  llm: GeminiLike,
  system: string,
  prompt: string,
  schema: z.ZodType<T>,
  clean: (raw: unknown) => unknown = (raw) => raw,
  deadline?: number,
): Promise<T> {
  const jsonSchema = jsonSchemaFor(schema);
  let attemptPrompt = prompt;
  for (let attempt = 0; attempt < 2; attempt++) {
    if (attempt > 0 && timeLeft(deadline) < RETRY_MIN_MS) throw new AgentError("timeout");
    const result = await llm.generate({
      system,
      prompt: attemptPrompt,
      jsonSchema,
      timeoutMs: callTimeout(deadline),
    });
    if (result.blocked) throw new AgentError("blocked");
    let problem = "";
    try {
      const parsed = schema.safeParse(clean(JSON.parse(result.text)));
      if (parsed.success) return parsed.data;
      problem = z.prettifyError(parsed.error);
    } catch (e) {
      problem = `not valid JSON (${e instanceof Error ? e.message : String(e)})`;
    }
    attemptPrompt = `${prompt}\n\nYour previous answer was rejected: ${problem}\nReturn JSON that satisfies the schema.`;
  }
  throw new AgentError("invalid_output");
}

function checkLimits(req: TurnRequest): void {
  const userMessages = req.messages.filter((m) => m.role === "user");
  if (userMessages.length > MAX_USER_MESSAGES) throw new AgentError("cap");
  if (req.messages.some((m) => m.text.length > MAX_MESSAGE_CHARS)) throw new AgentError("too_long");
}

function isHebrew(text: string): boolean {
  return /[֐-׿]/.test(text);
}

export async function runTurn(
  req: TurnRequest,
  llm: GeminiLike,
  newKey: () => string = () => crypto.randomUUID(),
  deadline?: number,
): Promise<TurnResult> {
  checkLimits(req);

  const latestUserMessage = [...req.messages].reverse().find((m) => m.role === "user")?.text ?? "";
  const hebrew = isHebrew(latestUserMessage);
  const geoNotice = hebrew ? GEO_NOTICE_TEXT_HE : GEO_NOTICE_TEXT;
  const blockedMessage = hebrew ? BLOCKED_TEXT_HE : USER_MESSAGES.blocked;

  const unchanged = (reply: string, research: Research | null): TurnResult => ({
    kind: "clarify",
    reply,
    candidates: [],
    drafts: req.drafts,
    research,
  });

  let research = req.research;
  try {
    const cls = await structured(
      llm,
      CLASSIFY_SYSTEM,
      classifyPrompt(req.messages, req.drafts.length > 0, research?.query ?? null),
      Classification,
      undefined,
      deadline,
    );

    if (cls.intent === "geography_only") return unchanged(geoNotice, research);

    const named = cls.intent === "company" || cls.intent === "pick_candidate";
    let researchedThisTurn = false;
    if (named && cls.company_query && cls.company_query !== research?.query) {
      if (timeLeft(deadline) < CALL_MIN_MS) throw new AgentError("timeout");
      const found = await llm.generate({
        system: RESEARCH_SYSTEM,
        prompt: researchPrompt(cls.company_query),
        search: true,
        timeoutMs: callTimeout(deadline),
      });
      if (found.blocked) return unchanged(blockedMessage, research);
      researchedThisTurn = true;
      research = {
        query: cls.company_query,
        text: found.text.slice(0, 20000),
        sources: numberSources(found.sources).slice(0, 40),
      };
    }

    if (timeLeft(deadline) < CALL_MIN_MS) throw new AgentError("timeout");
    const out = await structured(
      llm,
      DRAFT_SYSTEM,
      draftPrompt(req.messages, req.drafts, research, researchedThisTurn),
      ModelTurn,
      sanitizeModelTurn,
      deadline,
    );

    const usedKeys = new Set<string>();
    const drafts =
      out.kind === "drafts"
        ? out.drafts.map((d) => {
            const prior = req.drafts.find((p) => p.name === d.name && !usedKeys.has(p.key));
            const key = prior?.key ?? newKey();
            usedKeys.add(key);
            return toDraftView(d, research, key, researchedThisTurn);
          })
        : req.drafts;
    return {
      kind: out.kind,
      reply: cls.asks_geography ? `${geoNotice}\n\n${out.reply}` : out.reply,
      candidates: out.kind === "candidates" ? out.candidates : [],
      drafts,
      research,
    };
  } catch (e) {
    if (e instanceof AgentError && e.code === "blocked") return unchanged(blockedMessage, research);
    throw e;
  }
}
