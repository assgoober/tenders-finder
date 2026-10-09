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

/** Ask for JSON, validate it, retry once with the problem fed back. */
export async function structured<T>(
  llm: GeminiLike,
  system: string,
  prompt: string,
  schema: z.ZodType<T>,
  clean: (raw: unknown) => unknown = (raw) => raw,
): Promise<T> {
  const jsonSchema = jsonSchemaFor(schema);
  let attemptPrompt = prompt;
  for (let attempt = 0; attempt < 2; attempt++) {
    const result = await llm.generate({ system, prompt: attemptPrompt, jsonSchema });
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
    );

    if (cls.intent === "geography_only") return unchanged(geoNotice, research);

    const named = cls.intent === "company" || cls.intent === "pick_candidate";
    if (named && cls.company_query && cls.company_query !== research?.query) {
      const found = await llm.generate({
        system: RESEARCH_SYSTEM,
        prompt: researchPrompt(cls.company_query),
        search: true,
      });
      if (found.blocked) return unchanged(blockedMessage, research);
      research = {
        query: cls.company_query,
        text: found.text.slice(0, 20000),
        sources: numberSources(found.sources).slice(0, 40),
      };
    }

    const out = await structured(
      llm,
      DRAFT_SYSTEM,
      draftPrompt(req.messages, req.drafts, research),
      ModelTurn,
      sanitizeModelTurn,
    );

    const drafts =
      out.kind === "drafts"
        ? out.drafts.map((d) => {
            const prior = req.drafts.find((p) => p.name === d.name);
            return toDraftView(d, research, prior?.key ?? newKey());
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
