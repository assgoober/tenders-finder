/**
 * Everything the onboarding agent exchanges, as Zod schemas.
 *
 * Model output is never trusted (AGENTS.md): every response is parsed against
 * these before use. The draft limits are the form's own limits
 * (`lib/actions/profiles.ts`), so the chat cannot produce a profile the form
 * would refuse — `saveProfile` re-checks them on the server regardless.
 */
import { z } from "zod";

export const MAX_DRAFTS = 8;
export const MAX_KEYWORDS = 30;
export const MAX_USER_MESSAGES = 15;
export const MAX_MESSAGE_CHARS = 4000;

/** `%` and `_` are ILIKE wildcards in the matcher; `,` is the form's separator. */
const keyword = z
  .string()
  .min(1)
  .max(60)
  .regex(/^[^%_,\n]+$/, "keywords may not contain %, _, commas or newlines");

export const ModelDraft = z.object({
  name: z.string().trim().min(1).max(120),
  description: z.string().trim().min(40).max(4000),
  keywords: z.array(keyword).max(MAX_KEYWORDS),
  excluded_keywords: z.array(keyword).max(MAX_KEYWORDS),
  source_ids: z.array(z.number().int()).max(20),
  from_user: z.boolean(),
});
export type ModelDraft = z.infer<typeof ModelDraft>;

export const Candidate = z.object({
  name: z.string().min(1).max(200),
  country: z.string().max(100).nullable(),
  website: z.string().max(300).nullable(),
});
export type Candidate = z.infer<typeof Candidate>;

export const ModelTurn = z.object({
  kind: z.enum(["drafts", "candidates", "not_found", "clarify"]),
  reply: z.string().trim().min(1).max(4000),
  candidates: z.array(Candidate).max(8),
  drafts: z.array(ModelDraft).max(MAX_DRAFTS),
});
export type ModelTurn = z.infer<typeof ModelTurn>;

export const Classification = z.object({
  intent: z.enum(["company", "describe", "refine", "pick_candidate", "geography_only", "other"]),
  company_query: z.string().max(300).nullable(),
  asks_geography: z.boolean(),
});
export type Classification = z.infer<typeof Classification>;

export const Source = z.object({
  n: z.number().int().min(1),
  url: z.string().url().refine((u) => /^https?:\/\//i.test(u), "sources must be http(s) URLs"),
  title: z.string().max(300),
});
export type Source = z.infer<typeof Source>;

export const Research = z.object({
  query: z.string().max(300),
  text: z.string().max(20000),
  sources: z.array(Source).max(40),
});
export type Research = z.infer<typeof Research>;

export const Preview = z.discriminatedUnion("status", [
  z.object({
    status: z.literal("ok"),
    openTenders: z.number().int(),
    total: z.number().int(),
    top: z.array(
      z.object({
        id: z.string(),
        title: z.string(),
        deadline: z.string().nullable(),
        similarity: z.number(),
      }),
    ),
  }),
  z.object({ status: z.literal("empty_corpus") }),
  z.object({ status: z.literal("disabled"), reason: z.string() }),
  z.object({ status: z.literal("unavailable") }),
]);
export type Preview = z.infer<typeof Preview>;

export const DraftView = z.object({
  key: z.string().min(1).max(64),
  name: z.string().trim().min(1).max(120),
  description: z.string().trim().min(40).max(4000),
  keywords: z.array(keyword).max(MAX_KEYWORDS),
  excludedKeywords: z.array(keyword).max(MAX_KEYWORDS),
  sources: z.array(Source).max(20),
  unconfirmed: z.boolean(),
  preview: Preview.nullable(),
});
export type DraftView = z.infer<typeof DraftView>;

export const Message = z.object({
  role: z.enum(["user", "assistant"]),
  text: z.string().min(1),
});
export type Message = z.infer<typeof Message>;

export const TurnRequest = z.object({
  messages: z.array(Message).min(1).max(2 * MAX_USER_MESSAGES + 2),
  drafts: z.array(DraftView).max(MAX_DRAFTS),
  research: Research.nullable(),
});
export type TurnRequest = z.infer<typeof TurnRequest>;

/** Clean a keyword list BEFORE validation, so one stray "%" from the model
 *  costs a character instead of the whole turn. */
export function sanitizeKeywords(list: unknown): string[] {
  if (!Array.isArray(list)) return [];
  const seen = new Set<string>();
  const out: string[] = [];
  for (const item of list) {
    if (typeof item !== "string") continue;
    const word = item.replace(/[%_,\n]/g, " ").replace(/\s+/g, " ").trim().slice(0, 60).trim();
    const key = word.toLowerCase();
    if (!word || seen.has(key)) continue;
    seen.add(key);
    out.push(word);
    if (out.length === MAX_KEYWORDS) break;
  }
  return out;
}

/** Integers only, deduped, at most 20 — a stray "3" or 2.5 costs one id, not the turn. */
export function sanitizeSourceIds(list: unknown): number[] {
  if (!Array.isArray(list)) return [];
  const out: number[] = [];
  for (const item of list) {
    if (typeof item !== "number" || !Number.isInteger(item) || out.includes(item)) continue;
    out.push(item);
    if (out.length === 20) break;
  }
  return out;
}

export function sanitizeModelTurn(raw: unknown): unknown {
  if (!raw || typeof raw !== "object") return raw;
  const turn = raw as Record<string, unknown>;
  const drafts = Array.isArray(turn.drafts)
    ? turn.drafts.slice(0, MAX_DRAFTS).map((d) => {
        if (!d || typeof d !== "object") return d;
        const draft = d as Record<string, unknown>;
        return {
          ...draft,
          source_ids: sanitizeSourceIds(draft.source_ids),
          keywords: sanitizeKeywords(draft.keywords),
          excluded_keywords: sanitizeKeywords(draft.excluded_keywords),
        };
      })
    : turn.drafts;
  const candidates = Array.isArray(turn.candidates) ? turn.candidates.slice(0, 8) : turn.candidates;
  return { ...turn, drafts, candidates };
}
