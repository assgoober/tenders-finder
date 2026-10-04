# Onboarding Chat Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After login, a user names their company or describes what they want in a chat; a Gemini agent researches the company on the web, drafts one sourced alert profile per product line with a live match preview, and saves the ones the user approves.

**Architecture:** Everything runs in the Next.js app (`web/`). A stateless route handler classifies each message, runs a Google-Search-grounded research call when a company is named, then a tool-less structured call that drafts profiles citing sources by number; code (not the model) maps numbers to URLs and flags uncited drafts. Previews embed each draft with the sweep's exact recipe and call a read-only SQL function that mirrors `generate_matches_for_profile`. Saving reuses the existing `saveProfile` server action.

**Tech Stack:** Next.js 14 (App Router), TypeScript, `@google/genai`, `zod@^4`, `vitest`, Supabase (Postgres + pgvector), existing `@supabase/ssr` clients.

**Spec:** `docs/superpowers/specs/2026-10-04-onboarding-chat-design.md`

## Global Constraints

- Work only in `web/` and one new migration `supabase/migrations/0023_preview_profile_matches.sql`. No change to matching, the sweep, or alerts.
- Never write to Supabase project `vixuawfbayglbgitzqbj` (the original). Migrations apply only to the copy, `gfetpegbmlctsmpqagqi`.
- `GEMINI_API_KEY` is server-only — never in a `NEXT_PUBLIC_*` variable, never imported into a `"use client"` file.
- No model id in code. Chat model: `GEMINI_CHAT_MODEL`, falling back to `GEMINI_MODEL`. Embedding: `EMBEDDING_PROVIDER`, `GEMINI_EMBEDDING_MODEL`, `EMBEDDING_DIM` — the same variable names the scraper reads.
- Embedding recipe must equal `scraper-service/app/nlp/embeddings.py` for profiles: `task_type = RETRIEVAL_QUERY`, `output_dimensionality = EMBEDDING_DIM` (1024), then L2-normalize.
- Draft limits equal the form's (`web/lib/actions/profiles.ts`): name 1–120 chars; description 40–4000 chars; ≤30 keywords and ≤30 excluded keywords, each ≤60 chars, no `%`, `_` or `,`.
- Max 15 user messages per conversation; max 4000 characters per message; enforced on the server.
- Nothing persists before the user presses "Create profiles". No chat storage in the database.
- Every model output passes a Zod schema before use; on failure retry once with the error fed back; on a second failure, a friendly error and no draft.
- Geography is not a filter: the chat must say so and must not fold locations into descriptions or keywords.
- `npm run typecheck`, `npm run lint`, `npm test` and `npm run build` pass in `web/` at the end of every task that touches `web/`.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Partial save or double click on "Create profiles"** — a reasonable person expects each approved draft saved exactly once, and to be told which ones failed without re-saving the ones that worked. Pinned in Task 7 (`saveDrafts` tests).
2. **One malformed keyword from the model** (a `%`, `_`, comma, or a 31st keyword) — the user expects the turn to work, not "I couldn't put that together". Pinned in Task 1 (`sanitizeModelTurn` tests) and Task 3 (agent test).
3. **A pasted brochure longer than 4000 characters** — expected: a clear "too long" message and the text kept in the box, not a crash or a silent truncation. Pinned in Task 3 (`too_long`) and Task 6 (HTTP 413).
4. **Session expires mid-conversation** — expected: "you've been signed out" with a sign-in link, not a generic failure. Pinned in Task 6 (401 + message) and Task 7 (UI shows the link).
5. **A user who skips onboarding** — expected: not sent back to the chat on every visit. Pinned in Task 7 (`shouldOnboard` tests with the skip cookie).

## File Structure

| File | Responsibility |
|---|---|
| `web/vitest.config.mts` | Test runner config (node env, `@` alias). |
| `web/lib/onboarding/schema.ts` | Zod schemas, limits, keyword sanitizing. |
| `web/lib/onboarding/sources.ts` | Number grounding sources; attach sources to drafts; flag unconfirmed. |
| `web/lib/onboarding/errors.ts` | `AgentError`, codes, user-facing messages, HTTP statuses, `toAgentError`. |
| `web/lib/onboarding/config.ts` | Read env; say why chat/preview are unavailable. |
| `web/lib/onboarding/llm.ts` | `GeminiLike` interface (no SDK import), `normalize`. |
| `web/lib/onboarding/gemini.ts` | `@google/genai` adapter implementing `GeminiLike`. |
| `web/lib/onboarding/prompts.ts` | System prompts and prompt builders, `PROMPT_VERSION`. |
| `web/lib/onboarding/agent.ts` | `runTurn` and `structured` — the conversation logic. |
| `web/lib/onboarding/preview.ts` | `previewDraft`, `attachPreviews`. |
| `web/lib/onboarding/save.ts` | `saveDrafts` — save approved drafts once each. |
| `web/lib/onboarding/gate.ts` | `shouldOnboard`, `SKIP_COOKIE`. |
| `web/app/api/onboarding/route.ts` | POST one turn. |
| `web/app/api/onboarding/preview/route.ts` | POST one preview (inline-edited draft). |
| `web/app/onboarding/page.tsx` | Server page: auth, organization, render chat. |
| `web/components/onboarding-chat.tsx` | Client chat UI. |
| `web/app/page.tsx` | Redirect to onboarding when no profiles; "onboarded" notice. |
| `web/app/profiles/page.tsx` | "Set up with chat" link. |
| `web/tests/onboarding/*.test.ts` | Unit tests. |
| `supabase/migrations/0023_preview_profile_matches.sql` | Read-only preview function. |

---

### Task 1: Test tooling, schemas and source mapping

**Files:**
- Modify: `web/package.json` (dependencies, `test` script)
- Create: `web/vitest.config.mts`
- Create: `web/lib/onboarding/schema.ts`
- Create: `web/lib/onboarding/sources.ts`
- Test: `web/tests/onboarding/schema.test.ts`, `web/tests/onboarding/sources.test.ts`

**Interfaces:**
- Produces: `MAX_DRAFTS`, `MAX_KEYWORDS`, `MAX_USER_MESSAGES`, `MAX_MESSAGE_CHARS`; schemas `ModelDraft`, `ModelTurn`, `Classification`, `Source`, `Research`, `Preview`, `DraftView`, `Message`, `TurnRequest` and their inferred types; `sanitizeKeywords(list: unknown): string[]`; `sanitizeModelTurn(raw: unknown): unknown`; `numberSources(raw: {url: string; title: string}[]): Source[]`; `toDraftView(d: ModelDraft, research: Research | null, key: string): DraftView`.

- [ ] **Step 1: Install dependencies and add the test script**

Run (in `web/`):
```bash
npm install @google/genai zod@^4
npm install -D vitest
npm pkg set scripts.test="vitest run"
```
Expected: `package.json` lists `@google/genai`, `zod` (major 4) and devDependency `vitest`.

- [ ] **Step 2: Create `web/vitest.config.mts`**

```ts
import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "node",
    include: ["tests/**/*.test.ts"],
  },
  resolve: {
    alias: { "@": fileURLToPath(new URL(".", import.meta.url)) },
  },
});
```

- [ ] **Step 3: Write the failing tests**

`web/tests/onboarding/schema.test.ts`:
```ts
import { describe, expect, it } from "vitest";

import {
  MAX_KEYWORDS,
  ModelDraft,
  ModelTurn,
  sanitizeKeywords,
  sanitizeModelTurn,
} from "@/lib/onboarding/schema";

const DESC = "Tactical software-defined radios for armed forces and border police.";

function draft(over: Record<string, unknown> = {}) {
  return {
    name: "Radios",
    description: DESC,
    keywords: ["SDR"],
    excluded_keywords: [],
    source_ids: [1],
    from_user: false,
    ...over,
  };
}

describe("ModelDraft limits match the form", () => {
  it("accepts a valid draft", () => {
    expect(ModelDraft.safeParse(draft()).success).toBe(true);
  });
  it.each([
    ["empty name", { name: "" }],
    ["name over 120", { name: "x".repeat(121) }],
    ["description under 40", { description: "radios" }],
    ["description over 4000", { description: "x".repeat(4001) }],
    ["31 keywords", { keywords: Array.from({ length: 31 }, (_, i) => `k${i}`) }],
    ["keyword with %", { keywords: ["50%"] }],
    ["keyword with _", { keywords: ["a_b"] }],
    ["keyword with comma", { keywords: ["a,b"] }],
    ["keyword over 60", { keywords: ["x".repeat(61)] }],
  ])("rejects %s", (_label, over) => {
    expect(ModelDraft.safeParse(draft(over)).success).toBe(false);
  });
});

describe("sanitizing keeps one bad keyword from failing the turn", () => {
  it("strips wildcards and commas, trims, dedupes case-insensitively, caps the count", () => {
    const many = Array.from({ length: 40 }, (_, i) => `k${i}`);
    expect(sanitizeKeywords([" 50% ", "a_b", "x,y", "SDR", "sdr", "", 7])).toEqual([
      "50",
      "a b",
      "x y",
      "SDR",
    ]);
    expect(sanitizeKeywords(many)).toHaveLength(MAX_KEYWORDS);
    expect(sanitizeKeywords("not a list")).toEqual([]);
  });

  it("makes a turn with dirty keywords valid", () => {
    const raw = {
      kind: "drafts",
      reply: "Here is what I found.",
      candidates: [],
      drafts: [draft({ keywords: ["night_vision", "100%"] })],
    };
    expect(ModelTurn.safeParse(raw).success).toBe(false);
    const parsed = ModelTurn.safeParse(sanitizeModelTurn(raw));
    expect(parsed.success).toBe(true);
    expect(parsed.success && parsed.data.drafts[0].keywords).toEqual(["night vision", "100"]);
  });
});
```

`web/tests/onboarding/sources.test.ts`:
```ts
import { describe, expect, it } from "vitest";

import type { ModelDraft, Research } from "@/lib/onboarding/schema";
import { numberSources, toDraftView } from "@/lib/onboarding/sources";

const DESC = "Tactical software-defined radios for armed forces and border police.";

function draft(over: Partial<ModelDraft> = {}): ModelDraft {
  return {
    name: "Radios",
    description: DESC,
    keywords: [],
    excluded_keywords: [],
    source_ids: [],
    from_user: false,
    ...over,
  };
}

const research: Research = {
  query: "Elbit Systems",
  text: "Elbit makes radios.",
  sources: numberSources([
    { url: "https://elbitsystems.com/", title: "Elbit Systems" },
    { url: "https://example.org/radios", title: "Radios" },
  ]),
};

describe("numberSources", () => {
  it("numbers from 1 and drops duplicate URLs", () => {
    const out = numberSources([
      { url: "https://a.com/", title: "A" },
      { url: "https://a.com/", title: "A again" },
      { url: "https://b.com/", title: "" },
    ]);
    expect(out).toEqual([
      { n: 1, url: "https://a.com/", title: "A" },
      { n: 2, url: "https://b.com/", title: "https://b.com/" },
    ]);
  });
  it("drops entries that are not http(s) URLs", () => {
    expect(numberSources([{ url: "javascript:alert(1)", title: "x" }])).toEqual([]);
  });
});

describe("toDraftView", () => {
  it("maps cited numbers to the real URLs", () => {
    const view = toDraftView(draft({ source_ids: [2] }), research, "k1");
    expect(view.sources.map((s) => s.url)).toEqual(["https://example.org/radios"]);
    expect(view.unconfirmed).toBe(false);
  });
  it("flags a draft with no citation as unconfirmed", () => {
    expect(toDraftView(draft(), research, "k1").unconfirmed).toBe(true);
  });
  it("flags a draft citing only numbers that do not exist", () => {
    const view = toDraftView(draft({ source_ids: [9, 0, -1] }), research, "k1");
    expect(view.sources).toEqual([]);
    expect(view.unconfirmed).toBe(true);
  });
  it("does not flag what the user said themselves", () => {
    expect(toDraftView(draft({ from_user: true }), research, "k1").unconfirmed).toBe(false);
  });
  it("does not flag drafts when no research happened (direct description)", () => {
    const view = toDraftView(draft(), null, "k1");
    expect(view).toMatchObject({ sources: [], unconfirmed: false, preview: null, key: "k1" });
  });
  it("renames snake_case fields for the client", () => {
    const view = toDraftView(draft({ excluded_keywords: ["catering"] }), null, "k1");
    expect(view.excludedKeywords).toEqual(["catering"]);
  });
});
```

- [ ] **Step 4: Run tests to verify they fail**

Run (in `web/`): `npm test`
Expected: FAIL — cannot resolve `@/lib/onboarding/schema` / `@/lib/onboarding/sources`.

- [ ] **Step 5: Create `web/lib/onboarding/schema.ts`**

```ts
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
  url: z.string().url(),
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

export function sanitizeModelTurn(raw: unknown): unknown {
  if (!raw || typeof raw !== "object") return raw;
  const turn = raw as Record<string, unknown>;
  const drafts = Array.isArray(turn.drafts)
    ? turn.drafts.slice(0, MAX_DRAFTS).map((d) => {
        if (!d || typeof d !== "object") return d;
        const draft = d as Record<string, unknown>;
        return {
          ...draft,
          keywords: sanitizeKeywords(draft.keywords),
          excluded_keywords: sanitizeKeywords(draft.excluded_keywords),
        };
      })
    : turn.drafts;
  const candidates = Array.isArray(turn.candidates) ? turn.candidates.slice(0, 8) : turn.candidates;
  return { ...turn, drafts, candidates };
}
```

- [ ] **Step 6: Create `web/lib/onboarding/sources.ts`**

```ts
/**
 * Sources come from Google's grounding metadata, never from model text.
 *
 * The research call returns the pages it actually used; they are numbered
 * here. The drafting call may cite only those numbers, and this module turns
 * numbers back into links. A draft that cites nothing valid is flagged
 * `unconfirmed` by code — the model cannot vouch for itself.
 */
import type { DraftView, ModelDraft, Research, Source } from "./schema";

export function numberSources(raw: { url: string; title: string }[]): Source[] {
  const seen = new Set<string>();
  const out: Source[] = [];
  for (const { url, title } of raw) {
    if (!/^https?:\/\//i.test(url) || seen.has(url)) continue;
    seen.add(url);
    out.push({ n: out.length + 1, url, title: (title || url).slice(0, 300) });
  }
  return out;
}

export function toDraftView(d: ModelDraft, research: Research | null, key: string): DraftView {
  let sources: Source[] = [];
  if (research) {
    const byNumber = new Map(research.sources.map((s) => [s.n, s]));
    const cited = Array.from(new Set(d.source_ids));
    sources = cited.map((n) => byNumber.get(n)).filter((s): s is Source => s !== undefined);
  }
  return {
    key,
    name: d.name,
    description: d.description,
    keywords: d.keywords,
    excludedKeywords: d.excluded_keywords,
    sources,
    // No research means the user described it themselves: nothing to confirm.
    unconfirmed: research !== null && !d.from_user && sources.length === 0,
    preview: null,
  };
}
```

- [ ] **Step 7: Run tests to verify they pass**

Run (in `web/`): `npm test`
Expected: PASS (all tests in `schema.test.ts` and `sources.test.ts`).

- [ ] **Step 8: Typecheck and lint**

Run (in `web/`): `npm run typecheck && npm run lint`
Expected: no errors.

- [ ] **Step 9: Commit**

```bash
git add web/package.json web/package-lock.json web/vitest.config.mts web/lib/onboarding/schema.ts web/lib/onboarding/sources.ts web/tests/onboarding
git commit -m "Onboarding: schemas, keyword sanitizing and source mapping, with tests

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Config, errors and the Gemini adapter

**Files:**
- Create: `web/lib/onboarding/errors.ts`
- Create: `web/lib/onboarding/config.ts`
- Create: `web/lib/onboarding/llm.ts`
- Create: `web/lib/onboarding/gemini.ts`
- Test: `web/tests/onboarding/gemini.test.ts`, `web/tests/onboarding/config.test.ts`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces:
  - `type AgentErrorCode = "unavailable" | "quota" | "timeout" | "invalid_output" | "upstream" | "cap" | "too_long" | "blocked" | "signed_out" | "no_organization" | "bad_request"`; `class AgentError extends Error { code: AgentErrorCode }`; `USER_MESSAGES: Record<AgentErrorCode, string>`; `HTTP_STATUS: Record<AgentErrorCode, number>`; `toAgentError(e: unknown): AgentError`.
  - `type OnboardingConfig = { apiKey: string | null; chatModel: string | null; embeddingProvider: string | null; embeddingModel: string | null; embeddingDim: number }`; `readConfig(env?): OnboardingConfig`; `chatUnavailableReason(c): string | null`; `previewUnavailableReason(c): string | null`.
  - `type GenerateRequest = { system: string; prompt: string; search?: boolean; jsonSchema?: Record<string, unknown> }`; `type GenerateResult = { text: string; sources: { url: string; title: string }[]; blocked: boolean }`; `interface GeminiLike { generate(r: GenerateRequest): Promise<GenerateResult>; embed(text: string): Promise<number[]> }`; `normalize(v: number[]): number[]`.
  - `createGemini(config: OnboardingConfig, client?: GenAIClient): GeminiLike`.

- [ ] **Step 1: Confirm the installed SDK's shape**

Run (in `web/`):
```bash
grep -rn "googleSearch\|responseJsonSchema\|groundingChunks\|outputDimensionality\|taskType\|blockReason" node_modules/@google/genai/dist/genai.d.ts | head -20
grep -n "timeout" node_modules/@google/genai/dist/genai.d.ts | head -5
```
Expected: each of `googleSearch`, `responseJsonSchema`, `groundingChunks`, `outputDimensionality`, `taskType`, `blockReason` and an `httpOptions` `timeout` appears. If `responseJsonSchema` is absent, use `responseSchema` in Step 7 instead and note it in the commit message. If the `.d.ts` path differs, find it with `ls node_modules/@google/genai/dist/*.d.ts`.

- [ ] **Step 2: Write the failing tests**

`web/tests/onboarding/config.test.ts`:
```ts
import { describe, expect, it } from "vitest";

import {
  chatUnavailableReason,
  previewUnavailableReason,
  readConfig,
} from "@/lib/onboarding/config";

const FULL = {
  GEMINI_API_KEY: "k",
  GEMINI_CHAT_MODEL: "chat-model",
  EMBEDDING_PROVIDER: "gemini",
  GEMINI_EMBEDDING_MODEL: "gemini-embedding-001",
  EMBEDDING_DIM: "1024",
};

describe("config", () => {
  it("reads every setting", () => {
    expect(readConfig(FULL)).toEqual({
      apiKey: "k",
      chatModel: "chat-model",
      embeddingProvider: "gemini",
      embeddingModel: "gemini-embedding-001",
      embeddingDim: 1024,
    });
  });
  it("falls back to GEMINI_MODEL for chat and to 1024 dimensions", () => {
    const c = readConfig({ GEMINI_API_KEY: "k", GEMINI_MODEL: "m" });
    expect(c.chatModel).toBe("m");
    expect(c.embeddingDim).toBe(1024);
  });
  it("says why chat is unavailable", () => {
    expect(chatUnavailableReason(readConfig({}))).toMatch(/GEMINI_API_KEY/);
    expect(chatUnavailableReason(readConfig({ GEMINI_API_KEY: "k" }))).toMatch(/GEMINI_CHAT_MODEL/);
    expect(chatUnavailableReason(readConfig(FULL))).toBeNull();
  });
  it("disables the preview unless the embedding setup matches the sweep's", () => {
    expect(previewUnavailableReason(readConfig(FULL))).toBeNull();
    expect(previewUnavailableReason(readConfig({ ...FULL, EMBEDDING_PROVIDER: "voyage" }))).toMatch(
      /EMBEDDING_PROVIDER/,
    );
    expect(
      previewUnavailableReason(readConfig({ ...FULL, GEMINI_EMBEDDING_MODEL: "" })),
    ).toMatch(/GEMINI_EMBEDDING_MODEL/);
    expect(previewUnavailableReason(readConfig({ ...FULL, EMBEDDING_DIM: "768" }))).toMatch(/1024/);
  });
});
```

`web/tests/onboarding/gemini.test.ts`:
```ts
import { describe, expect, it, vi } from "vitest";

import { AgentError, toAgentError } from "@/lib/onboarding/errors";
import { createGemini, type GenAIClient } from "@/lib/onboarding/gemini";
import { normalize } from "@/lib/onboarding/llm";
import type { OnboardingConfig } from "@/lib/onboarding/config";

const CONFIG: OnboardingConfig = {
  apiKey: "k",
  chatModel: "chat-model",
  embeddingProvider: "gemini",
  embeddingModel: "gemini-embedding-001",
  embeddingDim: 1024,
};

function client(response: unknown, embedding: number[] = [3, 4]) {
  const generateContent = vi.fn().mockResolvedValue(response);
  const embedContent = vi.fn().mockResolvedValue({ embeddings: [{ values: embedding }] });
  return {
    fake: { models: { generateContent, embedContent } } as unknown as GenAIClient,
    generateContent,
    embedContent,
  };
}

describe("generate", () => {
  it("turns on Google Search and returns grounding sources, deduplicated", async () => {
    const { fake, generateContent } = client({
      text: "Elbit makes radios.",
      candidates: [
        {
          finishReason: "STOP",
          groundingMetadata: {
            groundingChunks: [
              { web: { uri: "https://a.com/", title: "A" } },
              { web: { uri: "https://a.com/", title: "A" } },
              { web: { uri: "https://b.com/" } },
              {},
            ],
          },
        },
      ],
    });
    const out = await createGemini(CONFIG, fake).generate({ system: "S", prompt: "P", search: true });
    const req = generateContent.mock.calls[0][0];
    expect(req.model).toBe("chat-model");
    expect(req.config.tools).toEqual([{ googleSearch: {} }]);
    expect(req.config.systemInstruction).toBe("S");
    expect(req.config.responseJsonSchema).toBeUndefined();
    expect(out).toEqual({
      text: "Elbit makes radios.",
      sources: [
        { url: "https://a.com/", title: "A" },
        { url: "https://b.com/", title: "https://b.com/" },
      ],
      blocked: false,
    });
  });

  it("asks for JSON against the schema, without tools", async () => {
    const { fake, generateContent } = client({ text: "{}", candidates: [{ finishReason: "STOP" }] });
    await createGemini(CONFIG, fake).generate({ system: "S", prompt: "P", jsonSchema: { type: "object" } });
    const req = generateContent.mock.calls[0][0];
    expect(req.config.responseMimeType).toBe("application/json");
    expect(req.config.responseJsonSchema).toEqual({ type: "object" });
    expect(req.config.tools).toBeUndefined();
  });

  it.each([
    [{ promptFeedback: { blockReason: "SAFETY" }, candidates: [] }],
    [{ text: "", candidates: [{ finishReason: "SAFETY" }] }],
    [{ text: "", candidates: [{ finishReason: "PROHIBITED_CONTENT" }] }],
  ])("reports a safety block as blocked, not as an error", async (response) => {
    const { fake } = client(response);
    const out = await createGemini(CONFIG, fake).generate({ system: "S", prompt: "P" });
    expect(out.blocked).toBe(true);
  });

  it("maps SDK failures to agent errors", async () => {
    const { fake, generateContent } = client({});
    generateContent.mockRejectedValueOnce(Object.assign(new Error("Too many"), { status: 429 }));
    await expect(createGemini(CONFIG, fake).generate({ system: "S", prompt: "P" })).rejects.toMatchObject({
      code: "quota",
    });
  });
});

describe("embed", () => {
  it("uses the sweep's recipe and normalizes", async () => {
    const { fake, embedContent } = client({}, [3, 4]);
    const v = await createGemini(CONFIG, fake).embed("radios");
    const req = embedContent.mock.calls[0][0];
    expect(req.model).toBe("gemini-embedding-001");
    expect(req.contents).toEqual(["radios"]);
    expect(req.config).toEqual({ taskType: "RETRIEVAL_QUERY", outputDimensionality: 1024 });
    expect(v).toEqual([0.6, 0.8]);
  });
});

describe("normalize", () => {
  it("scales to unit length and leaves a zero vector alone", () => {
    expect(normalize([3, 4])).toEqual([0.6, 0.8]);
    expect(normalize([0, 0])).toEqual([0, 0]);
  });
});

describe("toAgentError", () => {
  it.each([
    [Object.assign(new Error("x"), { status: 429 }), "quota"],
    [new Error("RESOURCE_EXHAUSTED: quota"), "quota"],
    [Object.assign(new Error("aborted"), { name: "AbortError" }), "timeout"],
    [new Error("request timed out"), "timeout"],
    [Object.assign(new Error("x"), { status: 403 }), "unavailable"],
    [new Error("API key not valid"), "unavailable"],
    [new Error("socket hang up"), "upstream"],
  ])("maps %s to %s", (err, code) => {
    expect(toAgentError(err).code).toBe(code);
  });
  it("passes an AgentError through", () => {
    const e = new AgentError("cap");
    expect(toAgentError(e)).toBe(e);
  });
});
```

- [ ] **Step 3: Run tests to verify they fail**

Run (in `web/`): `npm test`
Expected: FAIL — cannot resolve `@/lib/onboarding/errors`, `config`, `gemini`, `llm`.

- [ ] **Step 4: Create `web/lib/onboarding/errors.ts`**

```ts
/**
 * Every way a turn can fail, with the sentence the user sees and the HTTP
 * status the route returns. One table, so the route and the UI cannot drift
 * apart on wording.
 */
export type AgentErrorCode =
  | "unavailable"
  | "quota"
  | "timeout"
  | "invalid_output"
  | "upstream"
  | "cap"
  | "too_long"
  | "blocked"
  | "signed_out"
  | "no_organization"
  | "bad_request";

export class AgentError extends Error {
  constructor(public readonly code: AgentErrorCode, detail?: string) {
    super(detail ?? code);
    this.name = "AgentError";
  }
}

export const USER_MESSAGES: Record<AgentErrorCode, string> = {
  unavailable:
    "Chat setup isn't available right now. You can set up your profile with the form instead.",
  quota:
    "The AI service has reached its limit for now. Try again later, or set up your profile with the form.",
  timeout: "That took too long. Try again — your conversation is kept.",
  invalid_output: "I couldn't put that together. Try rephrasing.",
  upstream: "Something went wrong reaching the AI service. Try again.",
  cap: "This conversation has reached its length limit. Create the profiles you have, or start over.",
  too_long: "That message is too long — keep it under 4,000 characters.",
  blocked: "I can't look that up. Describe what your company makes instead.",
  signed_out: "You've been signed out. Sign in again — this conversation can't be kept.",
  no_organization: "Your account isn't a member of an organization yet.",
  bad_request: "Something about that request was malformed. Reload the page and try again.",
};

export const HTTP_STATUS: Record<AgentErrorCode, number> = {
  unavailable: 503,
  quota: 429,
  timeout: 504,
  invalid_output: 502,
  upstream: 502,
  cap: 429,
  too_long: 413,
  blocked: 422,
  signed_out: 401,
  no_organization: 403,
  bad_request: 400,
};

export function toAgentError(e: unknown): AgentError {
  if (e instanceof AgentError) return e;
  const err = (e ?? {}) as { status?: number; name?: string; message?: string };
  const status = typeof err.status === "number" ? err.status : undefined;
  const message = String(err.message ?? e);
  if (status === 429 || /RESOURCE_EXHAUSTED|quota/i.test(message)) return new AgentError("quota", message);
  if (err.name === "AbortError" || /timed? ?out|ETIMEDOUT|DEADLINE_EXCEEDED/i.test(message)) {
    return new AgentError("timeout", message);
  }
  if (status === 401 || status === 403 || /API key/i.test(message)) {
    return new AgentError("unavailable", message);
  }
  return new AgentError("upstream", message);
}
```

- [ ] **Step 5: Create `web/lib/onboarding/config.ts`**

```ts
/**
 * Settings for the onboarding agent, read from the environment.
 *
 * The embedding settings use the SAME variable names the scraper reads
 * (`scraper-service/app/config.py`), because the preview's vectors must be
 * comparable with the corpus the sweep embedded. A vector from another model
 * has the right shape and a meaningless score — so the preview is disabled,
 * not guessed, whenever these do not line up.
 */
export type OnboardingConfig = {
  apiKey: string | null;
  chatModel: string | null;
  embeddingProvider: string | null;
  embeddingModel: string | null;
  embeddingDim: number;
};

/** The `vector(1024)` columns in migrations 0003/0004 fix this. */
const DB_VECTOR_DIM = 1024;

export function readConfig(env: Record<string, string | undefined> = process.env): OnboardingConfig {
  const v = (name: string) => env[name]?.trim() || null;
  return {
    apiKey: v("GEMINI_API_KEY"),
    chatModel: v("GEMINI_CHAT_MODEL") ?? v("GEMINI_MODEL"),
    embeddingProvider: v("EMBEDDING_PROVIDER"),
    embeddingModel: v("GEMINI_EMBEDDING_MODEL"),
    embeddingDim: Number(v("EMBEDDING_DIM") ?? DB_VECTOR_DIM),
  };
}

export function chatUnavailableReason(c: OnboardingConfig): string | null {
  if (!c.apiKey) return "GEMINI_API_KEY is not set";
  if (!c.chatModel) return "GEMINI_CHAT_MODEL (or GEMINI_MODEL) is not set";
  return null;
}

export function previewUnavailableReason(c: OnboardingConfig): string | null {
  const chat = chatUnavailableReason(c);
  if (chat) return chat;
  if (c.embeddingProvider !== "gemini") {
    return "EMBEDDING_PROVIDER is not gemini, so a preview vector would not be comparable with the corpus";
  }
  if (!c.embeddingModel) return "GEMINI_EMBEDDING_MODEL is not set";
  if (c.embeddingDim !== DB_VECTOR_DIM) return `EMBEDDING_DIM must be ${DB_VECTOR_DIM}, the database column's size`;
  return null;
}
```

- [ ] **Step 6: Create `web/lib/onboarding/llm.ts`**

```ts
/**
 * The two things the agent needs from a model, without importing any SDK —
 * so the agent and its tests never touch the network.
 */
export type GenerateRequest = {
  system: string;
  prompt: string;
  /** Turn on Google Search grounding (research calls only). */
  search?: boolean;
  /** Ask for JSON matching this schema (structuring calls only). */
  jsonSchema?: Record<string, unknown>;
};

export type GenerateResult = {
  text: string;
  /** From grounding metadata — never parsed out of `text`. */
  sources: { url: string; title: string }[];
  blocked: boolean;
};

export interface GeminiLike {
  generate(request: GenerateRequest): Promise<GenerateResult>;
  embed(text: string): Promise<number[]>;
}

/** L2-normalize, as `scraper-service/app/nlp/embeddings.py::_normalize` does:
 *  a truncated Gemini vector is not unit length, and cosine thresholds assume it. */
export function normalize(v: number[]): number[] {
  const length = Math.sqrt(v.reduce((sum, x) => sum + x * x, 0));
  return length === 0 ? v : v.map((x) => x / length);
}
```

- [ ] **Step 7: Create `web/lib/onboarding/gemini.ts`**

```ts
/**
 * `@google/genai` behind the `GeminiLike` interface. Server-only: it reads
 * GEMINI_API_KEY and must never be imported by a "use client" file.
 */
import { GoogleGenAI } from "@google/genai";

import type { OnboardingConfig } from "./config";
import { toAgentError } from "./errors";
import { normalize, type GeminiLike, type GenerateRequest, type GenerateResult } from "./llm";

type ResponseLike = {
  text?: string;
  promptFeedback?: { blockReason?: string };
  candidates?: {
    finishReason?: string;
    groundingMetadata?: { groundingChunks?: { web?: { uri?: string; title?: string } }[] };
  }[];
};

/** The subset of the SDK client used here, so tests can pass a fake. */
export type GenAIClient = {
  models: {
    generateContent(args: unknown): Promise<ResponseLike>;
    embedContent(args: unknown): Promise<{ embeddings?: { values?: number[] }[] }>;
  };
};

const BLOCKING_FINISH = new Set(["SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII"]);

export function createGemini(config: OnboardingConfig, client?: GenAIClient): GeminiLike {
  const sdk: GenAIClient =
    client ??
    (new GoogleGenAI({
      apiKey: config.apiKey ?? "",
      httpOptions: { timeout: 45_000 },
    }) as unknown as GenAIClient);

  return {
    async generate(request: GenerateRequest): Promise<GenerateResult> {
      let response: ResponseLike;
      try {
        response = await sdk.models.generateContent({
          model: config.chatModel,
          contents: request.prompt,
          config: {
            systemInstruction: request.system,
            ...(request.search ? { tools: [{ googleSearch: {} }] } : {}),
            ...(request.jsonSchema
              ? { responseMimeType: "application/json", responseJsonSchema: request.jsonSchema }
              : {}),
          },
        });
      } catch (e) {
        throw toAgentError(e);
      }

      const candidate = response.candidates?.[0];
      const blocked =
        Boolean(response.promptFeedback?.blockReason) ||
        BLOCKING_FINISH.has(candidate?.finishReason ?? "");

      const seen = new Set<string>();
      const sources: { url: string; title: string }[] = [];
      for (const chunk of candidate?.groundingMetadata?.groundingChunks ?? []) {
        const uri = chunk.web?.uri;
        if (!uri || seen.has(uri)) continue;
        seen.add(uri);
        sources.push({ url: uri, title: chunk.web?.title || uri });
      }

      return { text: response.text ?? "", sources, blocked };
    },

    async embed(text: string): Promise<number[]> {
      try {
        const response = await sdk.models.embedContent({
          model: config.embeddingModel,
          contents: [text],
          config: { taskType: "RETRIEVAL_QUERY", outputDimensionality: config.embeddingDim },
        });
        return normalize(response.embeddings?.[0]?.values ?? []);
      } catch (e) {
        throw toAgentError(e);
      }
    },
  };
}
```

- [ ] **Step 8: Run tests to verify they pass**

Run (in `web/`): `npm test`
Expected: PASS (Task 1 and Task 2 tests).

- [ ] **Step 9: Typecheck and lint**

Run (in `web/`): `npm run typecheck && npm run lint`
Expected: no errors. If `new GoogleGenAI({... httpOptions: { timeout } })` fails to typecheck, check Step 1's output for the option's real name and use it.

- [ ] **Step 10: Commit**

```bash
git add web/lib/onboarding/errors.ts web/lib/onboarding/config.ts web/lib/onboarding/llm.ts web/lib/onboarding/gemini.ts web/tests/onboarding/config.test.ts web/tests/onboarding/gemini.test.ts
git commit -m "Onboarding: config, error table and the Gemini adapter, with tests

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: The agent — one conversation turn

**Files:**
- Create: `web/lib/onboarding/prompts.ts`
- Create: `web/lib/onboarding/agent.ts`
- Test: `web/tests/onboarding/agent.test.ts`

**Interfaces:**
- Consumes: Task 1 (`ModelTurn`, `Classification`, `Research`, `TurnRequest`, `DraftView`, `Candidate`, `sanitizeModelTurn`, `MAX_USER_MESSAGES`, `MAX_MESSAGE_CHARS`, `numberSources`, `toDraftView`); Task 2 (`GeminiLike`, `AgentError`, `USER_MESSAGES`).
- Produces: `type TurnResult = { kind: "drafts" | "candidates" | "not_found" | "clarify"; reply: string; candidates: Candidate[]; drafts: DraftView[]; research: Research | null }`; `runTurn(req: TurnRequest, llm: GeminiLike, newKey?: () => string): Promise<TurnResult>`; `structured<T>(llm, system, prompt, schema, clean?): Promise<T>`; `GEO_NOTICE: string`; `PROMPT_VERSION: string`.

- [ ] **Step 1: Write the failing tests**

`web/tests/onboarding/agent.test.ts`:
```ts
import { describe, expect, it } from "vitest";

import { GEO_NOTICE, runTurn } from "@/lib/onboarding/agent";
import { AgentError, USER_MESSAGES } from "@/lib/onboarding/errors";
import type { GeminiLike, GenerateRequest, GenerateResult } from "@/lib/onboarding/llm";
import type { DraftView, TurnRequest } from "@/lib/onboarding/schema";

const DESC_A = "Tactical software-defined radios for armed forces and border police.";
const DESC_B = "Unmanned aerial systems for surveillance, with ground control stations.";

type Step = Partial<GenerateResult> | Error;

function fake(steps: Step[]) {
  const calls: GenerateRequest[] = [];
  const llm: GeminiLike = {
    async generate(request) {
      calls.push(request);
      const next = steps.shift();
      if (next === undefined) throw new Error(`unexpected call #${calls.length}`);
      if (next instanceof Error) throw next;
      return { text: "", sources: [], blocked: false, ...next };
    },
    async embed() {
      return [];
    },
  };
  return { llm, calls };
}

const json = (value: unknown): Step => ({ text: JSON.stringify(value) });
const classify = (intent: string, company_query: string | null = null, asks_geography = false) =>
  json({ intent, company_query, asks_geography });
const modelDraft = (over: Record<string, unknown> = {}) => ({
  name: "Radios",
  description: DESC_A,
  keywords: ["SDR"],
  excluded_keywords: [],
  source_ids: [1],
  from_user: false,
  ...over,
});
const turn = (over: Record<string, unknown> = {}) =>
  json({ kind: "drafts", reply: "Here is what I found.", candidates: [], drafts: [modelDraft()], ...over });

function request(text: string, over: Partial<TurnRequest> = {}): TurnRequest {
  return { messages: [{ role: "user", text }], drafts: [], research: null, ...over };
}

let n = 0;
const key = () => `k${++n}`;

describe("company named", () => {
  it("researches with search, then drafts with real links and flags uncited lines", async () => {
    const { llm, calls } = fake([
      classify("company", "Elbit Systems"),
      {
        text: "Elbit Systems makes radios [1] and drones.",
        sources: [
          { url: "https://elbitsystems.com/", title: "Elbit Systems" },
          { url: "https://example.org/radios", title: "Radios" },
        ],
      },
      turn({
        drafts: [modelDraft(), modelDraft({ name: "UAV", description: DESC_B, source_ids: [] })],
      }),
    ]);

    const out = await runTurn(request("I work at Elbit Systems"), llm, key);

    expect(calls.map((c) => Boolean(c.search))).toEqual([false, true, false]);
    expect(calls[1].prompt).toContain("Elbit Systems");
    expect(out.kind).toBe("drafts");
    expect(out.research?.query).toBe("Elbit Systems");
    expect(out.drafts[0].sources[0].url).toBe("https://elbitsystems.com/");
    expect(out.drafts[0].unconfirmed).toBe(false);
    expect(out.drafts[1].unconfirmed).toBe(true);
    // The drafting prompt carries the numbered sources, so the model can cite them.
    expect(calls[2].prompt).toContain("[1] Elbit Systems — https://elbitsystems.com/");
  });

  it("returns candidates when several companies match", async () => {
    const { llm } = fake([
      classify("company", "Orbit"),
      { text: "Two companies named Orbit.", sources: [] },
      turn({
        kind: "candidates",
        reply: "Which one is yours?",
        drafts: [],
        candidates: [
          { name: "Orbit Communication Systems", country: "Israel", website: "orbit-cs.com" },
          { name: "Orbit Technologies", country: "USA", website: null },
        ],
      }),
    ]);
    const out = await runTurn(request("Orbit"), llm, key);
    expect(out.kind).toBe("candidates");
    expect(out.candidates).toHaveLength(2);
    expect(out.drafts).toEqual([]);
  });

  it("says not found instead of inventing", async () => {
    const { llm } = fake([
      classify("company", "Zzyzx Defence Ltd"),
      { text: "NOT_FOUND", sources: [] },
      turn({ kind: "not_found", reply: "I couldn't find it. What do you make?", drafts: [] }),
    ]);
    const out = await runTurn(request("Zzyzx Defence Ltd"), llm, key);
    expect(out.kind).toBe("not_found");
    expect(out.drafts).toEqual([]);
  });

  it("does not search again for the company it already researched", async () => {
    const research = { query: "Elbit Systems", text: "Elbit makes radios.", sources: [] };
    const { llm, calls } = fake([classify("company", "Elbit Systems"), turn()]);
    await runTurn(request("Elbit Systems again", { research }), llm, key);
    expect(calls.some((c) => c.search)).toBe(false);
  });

  it("asks the user to describe the company when research is blocked", async () => {
    const existing: DraftView[] = [];
    const { llm, calls } = fake([classify("company", "Something"), { blocked: true }]);
    const out = await runTurn(request("Something", { drafts: existing }), llm, key);
    expect(out.kind).toBe("clarify");
    expect(out.reply).toBe(USER_MESSAGES.blocked);
    expect(calls).toHaveLength(2);
  });
});

describe("direct description and corrections", () => {
  it("drafts without searching, and nothing is unconfirmed", async () => {
    const { llm, calls } = fake([
      classify("describe"),
      turn({ drafts: [modelDraft({ source_ids: [], from_user: true })] }),
    ]);
    const out = await runTurn(request("Tactical radios for armies"), llm, key);
    expect(calls).toHaveLength(2);
    expect(calls.some((c) => c.search)).toBe(false);
    expect(out.drafts[0]).toMatchObject({ sources: [], unconfirmed: false });
  });

  it("applies a correction without a new search", async () => {
    const research = { query: "Elbit Systems", text: "Elbit makes radios.", sources: [] };
    const { llm, calls } = fake([classify("refine"), turn()]);
    await runTurn(request("drop simulators", { research }), llm, key);
    expect(calls.some((c) => c.search)).toBe(false);
  });
});

describe("geography is not a filter", () => {
  const existing: DraftView[] = [
    {
      key: "d1",
      name: "Radios",
      description: DESC_A,
      keywords: [],
      excludedKeywords: [],
      sources: [],
      unconfirmed: false,
      preview: null,
    },
  ];

  it("answers a geography-only request without touching the drafts", async () => {
    const { llm, calls } = fake([classify("geography_only", null, true)]);
    const out = await runTurn(request("only Europe and Israel", { drafts: existing }), llm, key);
    expect(calls).toHaveLength(1);
    expect(out.reply).toBe(GEO_NOTICE);
    expect(out.drafts).toEqual(existing);
  });

  it("prefixes the notice when geography comes with a real correction", async () => {
    const { llm } = fake([classify("refine", null, true), turn({ reply: "Dropped simulators." })]);
    const out = await runTurn(request("drop simulators, only Europe", { drafts: existing }), llm, key);
    expect(out.reply.startsWith(GEO_NOTICE)).toBe(true);
    expect(out.reply).toContain("Dropped simulators.");
  });
});

describe("invalid model output", () => {
  it("retries once with the problem fed back, then succeeds", async () => {
    const { llm, calls } = fake([classify("describe"), { text: "not json" }, turn()]);
    const out = await runTurn(request("radios"), llm, key);
    expect(out.kind).toBe("drafts");
    expect(calls[2].prompt).toContain("was rejected");
  });

  it("fails with invalid_output after two bad answers", async () => {
    const { llm } = fake([classify("describe"), { text: "{}" }, { text: "{}" }]);
    await expect(runTurn(request("radios"), llm, key)).rejects.toMatchObject({ code: "invalid_output" });
  });

  it("survives a stray wildcard keyword from the model", async () => {
    const { llm } = fake([classify("describe"), turn({ drafts: [modelDraft({ keywords: ["night_vision", "50%"], source_ids: [], from_user: true })] })]);
    const out = await runTurn(request("night vision"), llm, key);
    expect(out.drafts[0].keywords).toEqual(["night vision", "50"]);
  });
});

describe("limits, enforced before any model call", () => {
  it("refuses a 16th user message", async () => {
    const messages = Array.from({ length: 16 }, (_, i) => ({ role: "user" as const, text: `m${i}` }));
    const { llm, calls } = fake([]);
    await expect(runTurn({ messages, drafts: [], research: null }, llm, key)).rejects.toMatchObject({
      code: "cap",
    });
    expect(calls).toHaveLength(0);
  });

  it("refuses a message over 4000 characters", async () => {
    const { llm, calls } = fake([]);
    const err = await runTurn(request("x".repeat(4001)), llm, key).catch((e) => e);
    expect(err).toBeInstanceOf(AgentError);
    expect(err.code).toBe("too_long");
    expect(calls).toHaveLength(0);
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run (in `web/`): `npm test -- tests/onboarding/agent.test.ts`
Expected: FAIL — cannot resolve `@/lib/onboarding/agent`.

- [ ] **Step 3: Create `web/lib/onboarding/prompts.ts`**

```ts
/**
 * Instructions for the onboarding agent. Versioned: a change here changes
 * what customers are shown, so it should be visible in history.
 */
import type { DraftView, Message, Research } from "./schema";

export const PROMPT_VERSION = "onboarding-2026-10-05";

export const CLASSIFY_SYSTEM = `You route messages in a sign-up chat for a public-procurement tender alert service.
Classify the user's LATEST message, using the conversation for context.
intent:
- "company": the user names the company they work for (or its website). Put the company name, plus website or country if given, in company_query.
- "pick_candidate": the user picks one of several companies the assistant listed. Put the chosen company, with its website or country, in company_query.
- "describe": the user describes products, capabilities or the tenders they want, without naming a company.
- "refine": the user corrects or adjusts drafts that already exist.
- "geography_only": the message only restricts countries or regions.
- "other": anything else.
asks_geography: true if the message asks to limit tenders by country, region or buyer location, in any language.
company_query is null unless intent is "company" or "pick_candidate".`;

export const RESEARCH_SYSTEM = `You research a company for a tender alert service. Use Google Search.
Report only what the sources say. Do not infer products or customers that no source states.
Organize by product line or business area: what it is, the technologies, and the kinds of buyers (armed forces, police, other government, civilian).
If several different companies match the name, write CANDIDATES and list each with country and website.
If you cannot identify the company, write exactly NOT_FOUND and nothing else.`;

export const DRAFT_SYSTEM = `You turn a sign-up conversation into alert profiles for a public-procurement tender service.
Tenders are matched against each profile's description by meaning, in any language. Keywords only slightly raise ranking. Excluded keywords veto tenders whose title or summary contains them.

Return JSON matching the schema. kind:
- "drafts": you can propose profiles. One profile per distinct product line or business area, at most 8.
- "candidates": the research lists several different companies and the user has not picked one. Fill candidates; drafts empty.
- "not_found": the research says NOT_FOUND. Ask what the company makes and who buys it; drafts empty.
- "clarify": you need one specific piece of information first; drafts unchanged or empty.

Each draft:
- name: short label for the product line.
- description: 2-5 sentences, a capability statement: products, technologies, typical buyers and contract types. No marketing language.
- keywords: up to 10 distinctive technical terms or standards. excluded_keywords: only themes the user rejected.
- source_ids: the numbers of the research sources this draft relies on. Cite only numbers that appear in the source list.
- from_user: true if the draft rests on what the user said rather than on research.

Rules:
- Never state a capability that neither the sources nor the user stated. Leaving something out is correct; guessing is a defect.
- Never add countries, regions or buyer locations to descriptions or keywords. The service cannot filter by location.
- On a correction, change only what the user asked to change and return all drafts, including unchanged ones.
- reply: short, in the language of the user's latest message. For drafts, say in one or two sentences what you based them on and ask the user to tick the ones they want or say what to change.`;

export const GEO_NOTICE_TEXT =
  "Filtering by country or region isn't supported yet — tenders are matched on what they're about, wherever the buyer is. I've left your profiles unchanged on that point.";

export function transcript(messages: Message[]): string {
  return messages
    .slice(-30)
    .map((m) => `${m.role === "user" ? "User" : "Assistant"}: ${m.text}`)
    .join("\n");
}

export function classifyPrompt(messages: Message[], hasDrafts: boolean, researched: string | null): string {
  return [
    `Conversation:\n${transcript(messages)}`,
    `Drafts exist: ${hasDrafts ? "yes" : "no"}`,
    `Already researched: ${researched ?? "nothing"}`,
  ].join("\n\n");
}

export function researchPrompt(query: string): string {
  return `Company: ${query}`;
}

export function draftPrompt(messages: Message[], drafts: DraftView[], research: Research | null): string {
  const sourceList = research?.sources.length
    ? research.sources.map((s) => `[${s.n}] ${s.title} — ${s.url}`).join("\n")
    : "(none)";
  const current = drafts.length
    ? JSON.stringify(
        drafts.map((d) => ({
          name: d.name,
          description: d.description,
          keywords: d.keywords,
          excluded_keywords: d.excludedKeywords,
        })),
        null,
        1,
      )
    : "(none)";
  return [
    `Conversation:\n${transcript(messages)}`,
    `Current drafts:\n${current}`,
    `Research${research ? ` on "${research.query}"` : ""}:\n${research?.text ?? "(none)"}`,
    `Sources:\n${sourceList}`,
  ].join("\n\n");
}
```

- [ ] **Step 4: Create `web/lib/onboarding/agent.ts`**

```ts
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
  CLASSIFY_SYSTEM,
  DRAFT_SYSTEM,
  GEO_NOTICE_TEXT,
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

export async function runTurn(
  req: TurnRequest,
  llm: GeminiLike,
  newKey: () => string = () => crypto.randomUUID(),
): Promise<TurnResult> {
  checkLimits(req);
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

    if (cls.intent === "geography_only") return unchanged(GEO_NOTICE, research);

    const named = cls.intent === "company" || cls.intent === "pick_candidate";
    if (named && cls.company_query && cls.company_query !== research?.query) {
      const found = await llm.generate({
        system: RESEARCH_SYSTEM,
        prompt: researchPrompt(cls.company_query),
        search: true,
      });
      if (found.blocked) return unchanged(USER_MESSAGES.blocked, research);
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
      out.kind === "drafts" ? out.drafts.map((d) => toDraftView(d, research, newKey())) : req.drafts;
    return {
      kind: out.kind,
      reply: cls.asks_geography ? `${GEO_NOTICE}\n\n${out.reply}` : out.reply,
      candidates: out.kind === "candidates" ? out.candidates : [],
      drafts,
      research,
    };
  } catch (e) {
    if (e instanceof AgentError && e.code === "blocked") return unchanged(USER_MESSAGES.blocked, research);
    throw e;
  }
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run (in `web/`): `npm test`
Expected: PASS (all onboarding tests).

- [ ] **Step 6: Typecheck and lint**

Run (in `web/`): `npm run typecheck && npm run lint`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add web/lib/onboarding/prompts.ts web/lib/onboarding/agent.ts web/tests/onboarding/agent.test.ts
git commit -m "Onboarding: the agent turn (classify, research, structure), with tests

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Migration 0023 — the preview function

**Files:**
- Create: `supabase/migrations/0023_preview_profile_matches.sql`
- Create (scratchpad, not committed): `preview_parity.py` in the session scratchpad directory

**Interfaces:**
- Produces: SQL `public.preview_profile_matches(p_embedding vector(1024), p_excluded_keywords text[] default '{}', p_limit integer default 3) returns jsonb` → `{ "open_tenders": int, "total": int, "cut": real, "top": [{ "id", "title", "deadline", "similarity" }] }`; executable by `authenticated`.

- [ ] **Step 1: Write the migration**

`supabase/migrations/0023_preview_profile_matches.sql`:
```sql
-- 0023_preview_profile_matches.sql
-- What a draft profile WOULD match right now — for the onboarding chat's
-- preview, before anything is saved.
--
-- MIRRORS, deliberately and line for line:
--   * generate_matches_for_profile (0015): open/closing_soon tenders with a
--     future (or no) deadline, similarity >= cut, excluded keywords vetoed on
--     title or summary;
--   * refresh_profile_score_cuts (0013): cut = percentile of similarity over
--     those same tenders, floored by min_score.
-- A change to either of those functions must be made here too, or the
-- preview will promise matches the sweep does not deliver. The constants are
-- the column defaults a new profile gets: min_score 0.58 (0013/0014) and
-- match_percentile 0.99 (0013).
--
-- SECURITY INVOKER: it reads only `tenders`, which `tenders_read` already
-- opens to any signed-in user. It writes nothing.

create or replace function public.preview_profile_matches(
  p_embedding         vector(1024),
  p_excluded_keywords text[]  default '{}',
  p_limit             integer default 3
)
returns jsonb
language sql
stable
security invoker
set search_path = public
as $$
  with open_tenders as (
    select t.id, t.title, t.summary, t.deadline,
           (1 - (t.embedding <=> p_embedding))::real as similarity
      from tenders t
     where t.embedding is not null
       and t.status in ('open', 'closing_soon')
       and (t.deadline is null or t.deadline > now())
  ),
  cut as (
    select greatest(
             0.58::real,
             coalesce(percentile_cont(0.99) within group (order by similarity), 0)::real
           ) as value
      from open_tenders
  ),
  hits as (
    select o.*
      from open_tenders o, cut
     where o.similarity >= cut.value
       and not exists (
         select 1 from unnest(coalesce(p_excluded_keywords, '{}')) x
          where o.title ilike '%' || x || '%'
             or coalesce(o.summary, '') ilike '%' || x || '%'
       )
  )
  select jsonb_build_object(
    'open_tenders', (select count(*) from open_tenders),
    'total',        (select count(*) from hits),
    'cut',          (select value from cut),
    'top', coalesce((
      select jsonb_agg(jsonb_build_object(
               'id', h.id, 'title', h.title, 'deadline', h.deadline,
               'similarity', round(h.similarity::numeric, 4))
             order by h.similarity desc)
        from (select * from hits
               order by similarity desc
               limit greatest(0, least(coalesce(p_limit, 3), 10))) h
    ), '[]'::jsonb)
  );
$$;

revoke execute on function public.preview_profile_matches(vector, text[], integer) from public;
revoke execute on function public.preview_profile_matches(vector, text[], integer) from anon;
grant  execute on function public.preview_profile_matches(vector, text[], integer) to authenticated, service_role;
```

- [ ] **Step 2: Apply to the copy only**

Run (repository root, Git Bash). The `.env` holds the copy's DB password; never point this at `vixuawfbayglbgitzqbj`:
```bash
PASS=$(grep ^SUPABASE_DB_PASSWORD .env | cut -d= -f2-)
echo Y | supabase db push --db-url "postgresql://postgres.gfetpegbmlctsmpqagqi:${PASS}@aws-1-eu-central-1.pooler.supabase.com:5432/postgres" 2>&1 | sed "s/$PASS/***/g" | tail -5
```
Expected: `Applying migration 0023_preview_profile_matches.sql...` then `Finished supabase db push.`

- [ ] **Step 3: Write the parity check (synthetic data, no Gemini needed)**

Save as `preview_parity.py` in the session scratchpad directory:
```python
"""Parity: preview_profile_matches must count exactly what
generate_matches_for_profile inserts. Synthetic vectors, cleaned up after."""
import json, math, random, secrets, urllib.request, urllib.error

ROOT = r"C:\lumosource\tenders-finder"
env = dict(l.strip().split("=", 1) for l in open(ROOT + r"\.env") if "=" in l and not l.startswith("#"))
URL, SRK = env["SUPABASE_URL"], env["SUPABASE_SERVICE_ROLE_KEY"]
assert "gfetpegbmlctsmpqagqi" in URL, "copy only"

def call(method, path, body=None, prefer=None):
    h = {"apikey": SRK, "Authorization": f"Bearer {SRK}", "Content-Type": "application/json"}
    if prefer: h["Prefer"] = prefer
    req = urllib.request.Request(URL + path, data=json.dumps(body).encode() if body is not None else None, headers=h, method=method)
    with urllib.request.urlopen(req) as r:
        t = r.read(); return json.loads(t) if t else None

def unit(v):
    n = math.sqrt(sum(x * x for x in v)); return [x / n for x in v]
def lit(v): return "[" + ",".join(f"{x:.7f}" for x in v) + "]"

random.seed(7)
base = unit([random.gauss(0, 1) for _ in range(1024)])
noise = unit([random.gauss(0, 1) for _ in range(1024)])
def mix(a): return unit([a * b + (1 - a) * c for b, c in zip(base, noise)])

tenders, org, prof = [], None, None
try:
    titles = ["Radio sets", "Radio catering services", "Radio spares", "Uniforms", "Boots"]
    weights = [1.0, 0.97, 0.9, 0.2, 0.0]
    for title, w in zip(titles, weights):
        row = call("POST", "/rest/v1/tenders?select=id", {"title": title, "source_url": f"https://example.com/{secrets.token_hex(4)}", "embedding": lit(mix(w))}, "return=representation")
        tenders.append(row[0]["id"])
    org = call("POST", "/rest/v1/organizations?select=id", {"name": "Parity Test Org"}, "return=representation")[0]["id"]
    for excluded in ([], ["catering"]):
        prof = call("POST", "/rest/v1/alert_profiles?select=id", {"organization_id": org, "name": "p", "description": "x" * 50, "excluded_keywords": excluded, "embedding": lit(base)}, "return=representation")[0]["id"]
        inserted = call("POST", "/rest/v1/rpc/generate_matches_for_profile", {"p_profile_id": prof})
        preview = call("POST", "/rest/v1/rpc/preview_profile_matches", {"p_embedding": lit(base), "p_excluded_keywords": excluded, "p_limit": 3})
        print(f"excluded={excluded}: generate={inserted} preview_total={preview['total']} open={preview['open_tenders']} cut={preview['cut']}")
        assert inserted == preview["total"], "PARITY FAILED"
        call("DELETE", f"/rest/v1/alert_profiles?id=eq.{prof}"); prof = None
    print("PARITY OK")
finally:
    if prof: call("DELETE", f"/rest/v1/alert_profiles?id=eq.{prof}")
    for t in tenders: call("DELETE", f"/rest/v1/tenders?id=eq.{t}")
    if org: call("DELETE", f"/rest/v1/organizations?id=eq.{org}")
    print("cleanup: tenders left =", len(call("GET", "/rest/v1/tenders?select=id")))
```

- [ ] **Step 4: Run the parity check**

Run: `python <scratchpad>/preview_parity.py`
Expected: two lines where `generate == preview_total` (the second with `catering` excluded, one fewer), then `PARITY OK`, then `cleanup: tenders left = 0` (or the count that existed before). If the tenders table rejects the insert for a missing column, read `supabase/migrations/0003_tenders.sql` for the `not null` columns and add them.

- [ ] **Step 5: Check that anonymous callers are refused**

Run (repository root, Git Bash):
```bash
ANON=$(grep ^NEXT_PUBLIC_SUPABASE_ANON_KEY web/.env.local | cut -d= -f2-)
curl -s -o /dev/null -w "%{http_code}
" -X POST   -H "apikey: $ANON" -H "Authorization: Bearer $ANON" -H "Content-Type: application/json"   -d '{"p_embedding":"[0]","p_excluded_keywords":[],"p_limit":1}'   https://gfetpegbmlctsmpqagqi.supabase.co/rest/v1/rpc/preview_profile_matches
```
Expected: `401` or `403` (execute is revoked from `anon`). The signed-in path is exercised end to end in Task 8's browser run.

- [ ] **Step 6: Commit**

```bash
git add supabase/migrations/0023_preview_profile_matches.sql
git commit -m "Migration 0023: read-only preview of what a draft profile would match

Mirrors generate_matches_for_profile and refresh_profile_score_cuts; parity
checked on the copy with synthetic vectors (with and without an excluded
keyword). Applied to the copy only.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Preview computation

**Files:**
- Create: `web/lib/onboarding/preview.ts`
- Test: `web/tests/onboarding/preview.test.ts`

**Interfaces:**
- Consumes: Task 1 (`Preview`, `DraftView`), Task 2 (`GeminiLike`, `OnboardingConfig`, `previewUnavailableReason`, `toAgentError`), Task 4 (SQL function name and result shape).
- Produces: `type RpcFn = (fn: string, args: Record<string, unknown>) => Promise<{ data: unknown; error: { message: string } | null }>`; `previewDraft(description: string, excluded: string[], deps: { llm: GeminiLike; rpc: RpcFn; config: OnboardingConfig }): Promise<Preview>`; `attachPreviews(drafts: DraftView[], previous: DraftView[], compute: (d: DraftView) => Promise<Preview>): Promise<DraftView[]>`.

- [ ] **Step 1: Write the failing tests**

`web/tests/onboarding/preview.test.ts`:
```ts
import { describe, expect, it, vi } from "vitest";

import type { OnboardingConfig } from "@/lib/onboarding/config";
import { AgentError } from "@/lib/onboarding/errors";
import type { GeminiLike } from "@/lib/onboarding/llm";
import { attachPreviews, previewDraft, type RpcFn } from "@/lib/onboarding/preview";
import type { DraftView, Preview } from "@/lib/onboarding/schema";

const CONFIG: OnboardingConfig = {
  apiKey: "k",
  chatModel: "m",
  embeddingProvider: "gemini",
  embeddingModel: "gemini-embedding-001",
  embeddingDim: 1024,
};
const VECTOR = Array.from({ length: 1024 }, () => 1 / 32);
const DESC = "Tactical software-defined radios for armed forces and border police.";

function llm(embed: () => Promise<number[]>): GeminiLike {
  return { generate: vi.fn(), embed: vi.fn(embed) };
}
const rpcReturning = (data: unknown, error: { message: string } | null = null): RpcFn =>
  vi.fn(async () => ({ data, error }));

describe("previewDraft", () => {
  it("is disabled, without embedding, when the embedding setup does not match the sweep", async () => {
    const model = llm(async () => VECTOR);
    const out = await previewDraft(DESC, [], {
      llm: model,
      rpc: rpcReturning(null),
      config: { ...CONFIG, embeddingProvider: "voyage" },
    });
    expect(out.status).toBe("disabled");
    expect(model.embed).not.toHaveBeenCalled();
  });

  it("is disabled when the vector has the wrong length", async () => {
    const out = await previewDraft(DESC, [], {
      llm: llm(async () => [1, 0]),
      rpc: rpcReturning(null),
      config: CONFIG,
    });
    expect(out).toMatchObject({ status: "disabled" });
  });

  it("is unavailable, not an error, when the embedding budget is spent", async () => {
    const out = await previewDraft(DESC, [], {
      llm: llm(async () => {
        throw new AgentError("quota");
      }),
      rpc: rpcReturning(null),
      config: CONFIG,
    });
    expect(out).toEqual({ status: "unavailable" });
  });

  it("is unavailable when the database call fails", async () => {
    const out = await previewDraft(DESC, [], {
      llm: llm(async () => VECTOR),
      rpc: rpcReturning(null, { message: "boom" }),
      config: CONFIG,
    });
    expect(out).toEqual({ status: "unavailable" });
  });

  it("says the corpus is empty rather than '0 matches'", async () => {
    const out = await previewDraft(DESC, [], {
      llm: llm(async () => VECTOR),
      rpc: rpcReturning({ open_tenders: 0, total: 0, cut: 0.58, top: [] }),
      config: CONFIG,
    });
    expect(out).toEqual({ status: "empty_corpus" });
  });

  it("calls the SQL function with the vector and exclusions, and maps the result", async () => {
    const rpc = rpcReturning({
      open_tenders: 900,
      total: 23,
      cut: 0.71,
      top: [{ id: "t1", title: "HF radios", deadline: "2026-11-01T00:00:00Z", similarity: 0.83 }],
    });
    const out = await previewDraft(DESC, ["catering"], { llm: llm(async () => VECTOR), rpc, config: CONFIG });
    const [fn, args] = (rpc as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(fn).toBe("preview_profile_matches");
    expect(args).toMatchObject({ p_excluded_keywords: ["catering"], p_limit: 3 });
    expect(String(args.p_embedding).startsWith("[")).toBe(true);
    expect(out).toEqual({
      status: "ok",
      openTenders: 900,
      total: 23,
      top: [{ id: "t1", title: "HF radios", deadline: "2026-11-01T00:00:00Z", similarity: 0.83 }],
    });
  });
});

describe("attachPreviews", () => {
  const ok: Preview = { status: "ok", openTenders: 10, total: 2, top: [] };
  const draft = (over: Partial<DraftView> = {}): DraftView => ({
    key: "k",
    name: "Radios",
    description: DESC,
    keywords: [],
    excludedKeywords: [],
    sources: [],
    unconfirmed: false,
    preview: null,
    ...over,
  });

  it("reuses a previous preview when description and exclusions are unchanged", async () => {
    const compute = vi.fn(async () => ok);
    const out = await attachPreviews([draft({ key: "new" })], [draft({ key: "old", preview: ok })], compute);
    expect(compute).not.toHaveBeenCalled();
    expect(out[0].preview).toEqual(ok);
  });

  it("recomputes when the description or the exclusions changed", async () => {
    const compute = vi.fn(async () => ok);
    await attachPreviews(
      [draft({ description: `${DESC} Also antennas.` }), draft({ key: "b", excludedKeywords: ["catering"] })],
      [draft({ preview: ok })],
      compute,
    );
    expect(compute).toHaveBeenCalledTimes(2);
  });

  it("retries a preview that was unavailable last time", async () => {
    const compute = vi.fn(async () => ok);
    await attachPreviews([draft()], [draft({ preview: { status: "unavailable" } })], compute);
    expect(compute).toHaveBeenCalledTimes(1);
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run (in `web/`): `npm test -- tests/onboarding/preview.test.ts`
Expected: FAIL — cannot resolve `@/lib/onboarding/preview`.

- [ ] **Step 3: Create `web/lib/onboarding/preview.ts`**

```ts
/**
 * "Would match 23 open tenders right now."
 *
 * The draft is embedded with the sweep's own recipe and scored by
 * `preview_profile_matches` (migration 0023), which mirrors the matcher. A
 * preview is a convenience: every failure degrades to a status the card can
 * show, never to an error that stops the conversation.
 */
import { previewUnavailableReason, type OnboardingConfig } from "./config";
import { toAgentError } from "./errors";
import type { GeminiLike } from "./llm";
import type { DraftView, Preview } from "./schema";

export type RpcFn = (
  fn: string,
  args: Record<string, unknown>,
) => Promise<{ data: unknown; error: { message: string } | null }>;

type SqlPreview = {
  open_tenders: number;
  total: number;
  cut: number;
  top: { id: string; title: string; deadline: string | null; similarity: number }[] | null;
};

export async function previewDraft(
  description: string,
  excluded: string[],
  deps: { llm: GeminiLike; rpc: RpcFn; config: OnboardingConfig },
): Promise<Preview> {
  const off = previewUnavailableReason(deps.config);
  if (off) return { status: "disabled", reason: off };

  let vector: number[];
  try {
    vector = await deps.llm.embed(description);
  } catch (e) {
    const err = toAgentError(e);
    return err.code === "unavailable"
      ? { status: "disabled", reason: "the embedding service rejected the key" }
      : { status: "unavailable" };
  }
  if (vector.length !== deps.config.embeddingDim) {
    return {
      status: "disabled",
      reason: `the embedding has ${vector.length} dimensions, expected ${deps.config.embeddingDim}`,
    };
  }

  const { data, error } = await deps.rpc("preview_profile_matches", {
    p_embedding: `[${vector.join(",")}]`,
    p_excluded_keywords: excluded,
    p_limit: 3,
  });
  if (error || !data) return { status: "unavailable" };

  const result = data as SqlPreview;
  if (result.open_tenders === 0) return { status: "empty_corpus" };
  return {
    status: "ok",
    openTenders: result.open_tenders,
    total: result.total,
    top: (result.top ?? []).map((t) => ({
      id: t.id,
      title: t.title,
      deadline: t.deadline,
      similarity: t.similarity,
    })),
  };
}

const sameInput = (a: DraftView, b: DraftView) =>
  a.description === b.description && a.excludedKeywords.join("\n") === b.excludedKeywords.join("\n");

export async function attachPreviews(
  drafts: DraftView[],
  previous: DraftView[],
  compute: (d: DraftView) => Promise<Preview>,
): Promise<DraftView[]> {
  return Promise.all(
    drafts.map(async (d) => {
      const reuse = previous.find(
        (p) => sameInput(p, d) && p.preview !== null && p.preview.status !== "unavailable",
      );
      return { ...d, preview: reuse ? reuse.preview : await compute(d) };
    }),
  );
}
```

- [ ] **Step 4: Run tests, typecheck, lint**

Run (in `web/`): `npm test && npm run typecheck && npm run lint`
Expected: all PASS, no errors.

- [ ] **Step 5: Commit**

```bash
git add web/lib/onboarding/preview.ts web/tests/onboarding/preview.test.ts
git commit -m "Onboarding: live match preview for draft profiles, with tests

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: API routes

**Files:**
- Create: `web/app/api/onboarding/route.ts`
- Create: `web/app/api/onboarding/preview/route.ts`
- Test: `web/tests/onboarding/route.test.ts`

**Interfaces:**
- Consumes: Tasks 1–5 (`TurnRequest`, `runTurn`, `createGemini`, `readConfig`, `chatUnavailableReason`, `previewDraft`, `attachPreviews`, `AgentError`, `HTTP_STATUS`, `USER_MESSAGES`, `toAgentError`, `MAX_MESSAGE_CHARS`); `createClient` from `@/lib/supabase/server`.
- Produces: `POST /api/onboarding` with body `TurnRequest` → 200 `TurnResult` (drafts with previews) or `{ error: { code, message } }` with `HTTP_STATUS[code]`. `POST /api/onboarding/preview` with body `{ description: string; excludedKeywords: string[] }` → 200 `{ preview: Preview }` or the same error shape.

- [ ] **Step 1: Write the failing tests**

`web/tests/onboarding/route.test.ts`:
```ts
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  user: { id: "u1" } as { id: string } | null,
  orgs: [{ id: "o1" }] as { id: string }[],
  runTurn: vi.fn(),
  previewDraft: vi.fn(),
}));

vi.mock("@/lib/supabase/server", () => ({
  createClient: async () => ({
    auth: { getUser: async () => ({ data: { user: mocks.user } }) },
    from: () => ({ select: () => ({ limit: async () => ({ data: mocks.orgs }) }) }),
    rpc: async () => ({ data: null, error: null }),
  }),
}));
vi.mock("@/lib/onboarding/gemini", () => ({ createGemini: () => ({}) }));
vi.mock("@/lib/onboarding/agent", () => ({ runTurn: mocks.runTurn }));
vi.mock("@/lib/onboarding/preview", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/onboarding/preview")>()),
  previewDraft: mocks.previewDraft,
}));

import { POST } from "@/app/api/onboarding/route";
import { POST as PREVIEW } from "@/app/api/onboarding/preview/route";
import { AgentError, USER_MESSAGES } from "@/lib/onboarding/errors";

const DESC = "Tactical software-defined radios for armed forces and border police.";
const body = (text = "I work at Elbit Systems") => ({
  messages: [{ role: "user", text }],
  drafts: [],
  research: null,
});
const post = (handler: (r: Request) => Promise<Response>, payload: unknown) =>
  handler(
    new Request("http://localhost/api/onboarding", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: typeof payload === "string" ? payload : JSON.stringify(payload),
    }),
  );

beforeEach(() => {
  mocks.user = { id: "u1" };
  mocks.orgs = [{ id: "o1" }];
  mocks.runTurn.mockReset();
  mocks.previewDraft.mockReset();
  vi.stubEnv("GEMINI_API_KEY", "k");
  vi.stubEnv("GEMINI_CHAT_MODEL", "m");
});

describe("POST /api/onboarding", () => {
  it("refuses a signed-out user with a sign-in message", async () => {
    mocks.user = null;
    const res = await post(POST, body());
    expect(res.status).toBe(401);
    expect((await res.json()).error).toEqual({ code: "signed_out", message: USER_MESSAGES.signed_out });
  });

  it("refuses a user with no organization", async () => {
    mocks.orgs = [];
    expect((await post(POST, body())).status).toBe(403);
  });

  it("says chat is unavailable when no key is configured", async () => {
    vi.stubEnv("GEMINI_API_KEY", "");
    const res = await post(POST, body());
    expect(res.status).toBe(503);
    expect((await res.json()).error.code).toBe("unavailable");
  });

  it("rejects a malformed body", async () => {
    expect((await post(POST, "not json")).status).toBe(400);
    expect((await post(POST, { messages: [] })).status).toBe(400);
  });

  it("returns 413 with a clear message for a pasted brochure", async () => {
    mocks.runTurn.mockRejectedValue(new AgentError("too_long"));
    const res = await post(POST, body("x".repeat(5000)));
    expect(res.status).toBe(413);
    expect((await res.json()).error.message).toBe(USER_MESSAGES.too_long);
  });

  it("returns 429 when the conversation is over its cap", async () => {
    mocks.runTurn.mockRejectedValue(new AgentError("cap"));
    expect((await post(POST, body())).status).toBe(429);
  });

  it("maps an unexpected failure to a friendly upstream error", async () => {
    mocks.runTurn.mockRejectedValue(new Error("socket hang up"));
    const res = await post(POST, body());
    expect(res.status).toBe(502);
    expect((await res.json()).error.code).toBe("upstream");
  });

  it("returns the turn with previews attached", async () => {
    mocks.runTurn.mockResolvedValue({
      kind: "drafts",
      reply: "Found it.",
      candidates: [],
      research: null,
      drafts: [
        {
          key: "d1",
          name: "Radios",
          description: DESC,
          keywords: [],
          excludedKeywords: [],
          sources: [],
          unconfirmed: false,
          preview: null,
        },
      ],
    });
    mocks.previewDraft.mockResolvedValue({ status: "empty_corpus" });
    const res = await post(POST, body());
    expect(res.status).toBe(200);
    const json = await res.json();
    expect(json.reply).toBe("Found it.");
    expect(json.drafts[0].preview).toEqual({ status: "empty_corpus" });
  });
});

describe("POST /api/onboarding/preview", () => {
  it("refuses a signed-out user", async () => {
    mocks.user = null;
    expect((await post(PREVIEW, { description: DESC, excludedKeywords: [] })).status).toBe(401);
  });

  it("rejects a description the form would refuse", async () => {
    expect((await post(PREVIEW, { description: "radios", excludedKeywords: [] })).status).toBe(400);
  });

  it("returns the preview", async () => {
    mocks.previewDraft.mockResolvedValue({ status: "unavailable" });
    const res = await post(PREVIEW, { description: DESC, excludedKeywords: ["catering"] });
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ preview: { status: "unavailable" } });
    expect(mocks.previewDraft.mock.calls[0][1]).toEqual(["catering"]);
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run (in `web/`): `npm test -- tests/onboarding/route.test.ts`
Expected: FAIL — cannot resolve `@/app/api/onboarding/route`.

- [ ] **Step 3: Create `web/app/api/onboarding/route.ts`**

```ts
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
```

- [ ] **Step 4: Create `web/app/api/onboarding/preview/route.ts`**

```ts
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
```

- [ ] **Step 5: Run tests, typecheck, lint**

Run (in `web/`): `npm test && npm run typecheck && npm run lint`
Expected: all PASS, no errors.

- [ ] **Step 6: Commit**

```bash
git add web/app/api/onboarding web/tests/onboarding/route.test.ts
git commit -m "Onboarding: turn and preview API routes, with tests

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Saving, the chat UI, and the post-login redirect

**Files:**
- Create: `web/lib/onboarding/save.ts`
- Create: `web/lib/onboarding/gate.ts`
- Create: `web/app/onboarding/page.tsx`
- Create: `web/components/onboarding-chat.tsx`
- Modify: `web/app/page.tsx` (signature, redirect, notice)
- Modify: `web/app/profiles/page.tsx` (link)
- Test: `web/tests/onboarding/save.test.ts`, `web/tests/onboarding/gate.test.ts`

**Interfaces:**
- Consumes: Task 1 (`DraftView`, `Preview`, `Research`, `Candidate`), Task 2 (`readConfig`, `chatUnavailableReason`), Task 6 (route shapes); `saveProfile`, `ProfileInput`, `ProfileResult` from `@/lib/actions/profiles`.
- Produces: `type SaveOutcome = { key: string; ok: true; id: string } | { key: string; ok: false; error: string }`; `saveDrafts(drafts: DraftView[], alreadySaved: ReadonlySet<string>, save: (i: ProfileInput) => Promise<ProfileResult>): Promise<SaveOutcome[]>`; `SKIP_COOKIE = "tf_onboarding_skipped"`; `shouldOnboard(s: { hasOrganization: boolean; hasProfile: boolean; skipped: boolean }): boolean`.

- [ ] **Step 1: Write the failing tests**

`web/tests/onboarding/save.test.ts`:
```ts
import { describe, expect, it, vi } from "vitest";

import type { ProfileInput } from "@/lib/actions/profiles";
import type { DraftView } from "@/lib/onboarding/schema";
import { saveDrafts } from "@/lib/onboarding/save";

const DESC = "Tactical software-defined radios for armed forces and border police.";
const draft = (key: string): DraftView => ({
  key,
  name: `Profile ${key}`,
  description: DESC,
  keywords: ["SDR", "HF"],
  excludedKeywords: ["catering"],
  sources: [],
  unconfirmed: false,
  preview: null,
});

describe("saveDrafts", () => {
  it("saves each draft once, in the form's input shape", async () => {
    const save = vi.fn(async (_input: ProfileInput) => ({ ok: true as const, id: "p1" }));
    const out = await saveDrafts([draft("a")], new Set(), save);
    expect(save).toHaveBeenCalledWith({
      name: "Profile a",
      description: DESC,
      keywords: "SDR, HF",
      excludedKeywords: "catering",
      notifyEmail: true,
    });
    expect(out).toEqual([{ key: "a", ok: true, id: "p1" }]);
  });

  it("reports a partial failure per draft and keeps going", async () => {
    const save = vi
      .fn()
      .mockResolvedValueOnce({ ok: true, id: "p1" })
      .mockResolvedValueOnce({ ok: false, error: "nope" })
      .mockRejectedValueOnce(new Error("network"));
    const out = await saveDrafts([draft("a"), draft("b"), draft("c")], new Set(), save);
    expect(out).toEqual([
      { key: "a", ok: true, id: "p1" },
      { key: "b", ok: false, error: "nope" },
      { key: "c", ok: false, error: "network" },
    ]);
  });

  it("never saves a draft twice on a retry", async () => {
    const save = vi.fn(async (_input: ProfileInput) => ({ ok: true as const, id: "p2" }));
    await saveDrafts([draft("a"), draft("b")], new Set(["a"]), save);
    expect(save).toHaveBeenCalledTimes(1);
    expect(save.mock.calls[0][0]).toMatchObject({ name: "Profile b" });
  });
});
```

`web/tests/onboarding/gate.test.ts`:
```ts
import { describe, expect, it } from "vitest";

import { shouldOnboard } from "@/lib/onboarding/gate";

describe("shouldOnboard", () => {
  it("sends a member with no profiles to onboarding", () => {
    expect(shouldOnboard({ hasOrganization: true, hasProfile: false, skipped: false })).toBe(true);
  });
  it("leaves alone anyone who already has a profile", () => {
    expect(shouldOnboard({ hasOrganization: true, hasProfile: true, skipped: false })).toBe(false);
  });
  it("respects a skip, so a user is not sent back on every visit", () => {
    expect(shouldOnboard({ hasOrganization: true, hasProfile: false, skipped: true })).toBe(false);
  });
  it("does nothing for a user with no organization (nowhere to save)", () => {
    expect(shouldOnboard({ hasOrganization: false, hasProfile: false, skipped: false })).toBe(false);
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run (in `web/`): `npm test -- tests/onboarding/save.test.ts tests/onboarding/gate.test.ts`
Expected: FAIL — cannot resolve `@/lib/onboarding/save` / `gate`.

- [ ] **Step 3: Create `web/lib/onboarding/save.ts` and `web/lib/onboarding/gate.ts`**

`web/lib/onboarding/save.ts`:
```ts
/**
 * Save the drafts the user approved, through the same server action as the
 * form. Each draft at most once: a retry after a partial failure skips the
 * ones that already went in.
 */
import type { ProfileInput, ProfileResult } from "@/lib/actions/profiles";

import type { DraftView } from "./schema";

export type SaveOutcome = { key: string; ok: true; id: string } | { key: string; ok: false; error: string };

export async function saveDrafts(
  drafts: DraftView[],
  alreadySaved: ReadonlySet<string>,
  save: (input: ProfileInput) => Promise<ProfileResult>,
): Promise<SaveOutcome[]> {
  const outcomes: SaveOutcome[] = [];
  for (const d of drafts) {
    if (alreadySaved.has(d.key)) continue;
    try {
      const result = await save({
        name: d.name,
        description: d.description,
        keywords: d.keywords.join(", "),
        excludedKeywords: d.excludedKeywords.join(", "),
        notifyEmail: true,
      });
      outcomes.push(result.ok ? { key: d.key, ok: true, id: result.id } : { key: d.key, ok: false, error: result.error });
    } catch (e) {
      outcomes.push({ key: d.key, ok: false, error: e instanceof Error ? e.message : "Could not save." });
    }
  }
  return outcomes;
}
```

`web/lib/onboarding/gate.ts`:
```ts
/** Who is sent to the onboarding chat after login. */
export const SKIP_COOKIE = "tf_onboarding_skipped";

export function shouldOnboard(s: { hasOrganization: boolean; hasProfile: boolean; skipped: boolean }): boolean {
  return s.hasOrganization && !s.hasProfile && !s.skipped;
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run (in `web/`): `npm test`
Expected: PASS.

- [ ] **Step 5: Create `web/app/onboarding/page.tsx`**

```tsx
/**
 * Onboarding: tell us your company, or what you want, in your own words.
 * Requires sign-in and an organization (there is nowhere else to save).
 */
import { redirect } from "next/navigation";

import { OnboardingChat } from "@/components/onboarding-chat";
import { chatUnavailableReason, readConfig } from "@/lib/onboarding/config";
import { isSupabaseConfigured } from "@/lib/supabase/config";
import { createClient } from "@/lib/supabase/server";

export const dynamic = "force-dynamic";

export default async function OnboardingPage() {
  if (!isSupabaseConfigured) redirect("/");

  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login");

  const { data: orgs } = await supabase.from("organizations").select("id").limit(1);
  if (!orgs || orgs.length === 0) redirect("/profiles");

  return (
    <main className="mx-auto max-w-3xl px-6 py-8">
      <h1 className="text-xl font-semibold text-slate-900">Set up your tender alerts</h1>
      <p className="mb-6 mt-1 text-sm text-slate-500">
        Tell me which company you work for, or describe the tenders you want. I&apos;ll draft your
        alert profiles — nothing is saved until you approve it.
      </p>
      <OnboardingChat unavailable={chatUnavailableReason(readConfig()) !== null} />
    </main>
  );
}
```

- [ ] **Step 6: Create `web/components/onboarding-chat.tsx`**

```tsx
"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { saveProfile } from "@/lib/actions/profiles";
import { SKIP_COOKIE } from "@/lib/onboarding/gate";
import { saveDrafts } from "@/lib/onboarding/save";
import type { Candidate, DraftView, Preview, Research } from "@/lib/onboarding/schema";

type Msg = { role: "user" | "assistant"; text: string };
type DraftCard = DraftView & { checked: boolean };
type ApiError = { code: string; message: string };

const INPUT =
  "w-full rounded-md border border-slate-300 px-3 py-2 text-sm outline-none focus:border-slate-900 focus:ring-1 focus:ring-slate-900";

const toView = (d: DraftCard): DraftView => ({
  key: d.key,
  name: d.name,
  description: d.description,
  keywords: d.keywords,
  excludedKeywords: d.excludedKeywords,
  sources: d.sources,
  unconfirmed: d.unconfirmed,
  preview: d.preview,
});

function PreviewBox({ preview }: { preview: Preview | null }) {
  if (!preview) return null;
  if (preview.status === "empty_corpus") {
    return <p className="text-xs text-slate-500">No open tenders in the database yet, so nothing to preview.</p>;
  }
  if (preview.status === "disabled") {
    return <p className="text-xs text-slate-500">Preview off: {preview.reason}.</p>;
  }
  if (preview.status === "unavailable") {
    return <p className="text-xs text-slate-500">Preview unavailable right now — try again later.</p>;
  }
  return (
    <div className="rounded-md bg-slate-50 px-3 py-2 text-xs text-slate-700">
      <p className="font-medium">
        Would match {preview.total} of {preview.openTenders} open tenders right now
      </p>
      {preview.top.length > 0 ? (
        <ul className="mt-1 list-disc pl-4">
          {preview.top.map((t) => (
            <li key={t.id} dir="auto">
              {t.title}
              {t.deadline ? ` — closes ${new Date(t.deadline).toLocaleDateString()}` : ""}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

export function OnboardingChat({ unavailable }: { unavailable: boolean }) {
  const router = useRouter();
  const [messages, setMessages] = useState<Msg[]>([]);
  const [drafts, setDrafts] = useState<DraftCard[]>([]);
  const [research, setResearch] = useState<Research | null>(null);
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState<ReadonlySet<string>>(new Set());
  const [saveErrors, setSaveErrors] = useState<Record<string, string>>({});

  const unsaved = drafts.some((d) => d.checked && !saved.has(d.key));
  useEffect(() => {
    if (!unsaved) return;
    const warn = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [unsaved]);

  function skip() {
    document.cookie = `${SKIP_COOKIE}=1; path=/; max-age=31536000; samesite=lax`;
    router.push("/");
    router.refresh();
  }

  async function send(text: string) {
    const trimmed = text.trim();
    if (!trimmed || busy) return;
    const previous = messages;
    const next: Msg[] = [...messages, { role: "user", text: trimmed }];
    setMessages(next);
    setInput("");
    setError(null);
    setBusy(true);
    try {
      const res = await fetch("/api/onboarding", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ messages: next, drafts: drafts.map(toView), research }),
      });
      const body = await res.json();
      if (!res.ok) {
        setError(body.error as ApiError);
        setMessages(previous);
        setInput(trimmed);
        return;
      }
      setMessages([...next, { role: "assistant", text: body.reply as string }]);
      setResearch(body.research as Research | null);
      setCandidates((body.candidates as Candidate[]) ?? []);
      setDrafts(
        (body.drafts as DraftView[]).map((d) => ({
          ...d,
          checked: drafts.find((p) => p.key === d.key)?.checked ?? !d.unconfirmed,
        })),
      );
    } catch {
      setError({ code: "network", message: "Could not reach the server. Check your connection and try again." });
      setMessages(previous);
      setInput(trimmed);
    } finally {
      setBusy(false);
    }
  }

  function patch(key: string, change: Partial<DraftCard>) {
    setDrafts((ds) => ds.map((d) => (d.key === key ? { ...d, ...change } : d)));
  }

  async function refreshPreview(d: DraftCard) {
    if (d.description.trim().length < 40) return;
    try {
      const res = await fetch("/api/onboarding/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ description: d.description, excludedKeywords: d.excludedKeywords }),
      });
      if (res.ok) patch(d.key, { preview: (await res.json()).preview as Preview });
    } catch {
      // Keep the previous preview; a preview is a convenience, not a gate.
    }
  }

  async function create() {
    if (saving) return;
    setSaving(true);
    setSaveErrors({});
    const chosen = drafts.filter((d) => d.checked).map(toView);
    const outcomes = await saveDrafts(chosen, saved, saveProfile);
    const nowSaved = new Set(saved);
    const errors: Record<string, string> = {};
    for (const o of outcomes) {
      if (o.ok) nowSaved.add(o.key);
      else errors[o.key] = o.error;
    }
    setSaved(nowSaved);
    setSaveErrors(errors);
    setSaving(false);
    if (Object.keys(errors).length === 0) {
      router.push("/?onboarded=1");
      router.refresh();
    }
  }

  if (unavailable) {
    return (
      <Card>
        <CardContent className="space-y-3 py-5 text-sm text-slate-700">
          <p>Chat setup isn&apos;t available here. You can describe your company in the form instead.</p>
          <div className="flex gap-2">
            <Link href="/profiles" className="underline">
              Set up with the form
            </Link>
            <button type="button" onClick={skip} className="text-slate-500 underline">
              Skip for now
            </button>
          </div>
        </CardContent>
      </Card>
    );
  }

  const checkedCount = drafts.filter((d) => d.checked && !saved.has(d.key)).length;

  return (
    <div className="space-y-4">
      <div className="space-y-3">
        {messages.map((m, i) => (
          <p
            key={i}
            dir="auto"
            className={
              m.role === "user"
                ? "ml-auto max-w-[85%] whitespace-pre-line rounded-md bg-slate-900 px-3 py-2 text-sm text-white"
                : "max-w-[85%] whitespace-pre-line rounded-md bg-slate-100 px-3 py-2 text-sm text-slate-800"
            }
          >
            {m.text}
          </p>
        ))}
        {busy ? <p className="text-sm text-slate-500">Looking into it…</p> : null}
      </div>

      {candidates.length > 0 ? (
        <div className="flex flex-wrap gap-2">
          {candidates.map((c) => (
            <Button
              key={`${c.name}-${c.website ?? ""}`}
              type="button"
              variant="outline"
              disabled={busy}
              onClick={() => send(`It's ${c.name}${c.website ? ` (${c.website})` : ""}${c.country ? `, ${c.country}` : ""}`)}
            >
              {c.name}
              {c.country ? ` · ${c.country}` : ""}
            </Button>
          ))}
        </div>
      ) : null}

      {drafts.map((d) => (
        <Card key={d.key}>
          <CardContent className="space-y-2 py-4">
            <label className="flex items-center gap-2 text-sm font-semibold text-slate-900">
              <input
                type="checkbox"
                checked={d.checked}
                disabled={saved.has(d.key)}
                onChange={(e) => patch(d.key, { checked: e.target.checked })}
              />
              <span dir="auto">{d.name}</span>
              {saved.has(d.key) ? <span className="text-xs font-normal text-green-700">Saved</span> : null}
            </label>
            {d.unconfirmed ? (
              <p className="text-xs text-amber-700">Unconfirmed — no source backs this. Check it before saving.</p>
            ) : null}
            <textarea
              dir="auto"
              rows={4}
              value={d.description}
              disabled={saved.has(d.key)}
              onChange={(e) => patch(d.key, { description: e.target.value })}
              onBlur={() => refreshPreview(d)}
              className={INPUT}
            />
            {d.keywords.length > 0 ? <p className="text-xs text-slate-500">Keywords: {d.keywords.join(", ")}</p> : null}
            {d.excludedKeywords.length > 0 ? (
              <p className="text-xs text-slate-500">Never: {d.excludedKeywords.join(", ")}</p>
            ) : null}
            {d.sources.length > 0 ? (
              <p className="text-xs text-slate-500">
                Sources:{" "}
                {d.sources.map((s, i) => (
                  <span key={s.url}>
                    {i > 0 ? ", " : ""}
                    <a href={s.url} target="_blank" rel="noreferrer noopener" className="underline">
                      {s.title}
                    </a>
                  </span>
                ))}
              </p>
            ) : null}
            <PreviewBox preview={d.preview} />
            {saveErrors[d.key] ? (
              <p role="alert" className="text-sm text-red-600">
                {saveErrors[d.key]}
              </p>
            ) : null}
          </CardContent>
        </Card>
      ))}

      {error ? (
        <p role="alert" className="text-sm text-red-600">
          {error.message}{" "}
          {error.code === "signed_out" ? (
            <Link href="/login" className="underline">
              Sign in
            </Link>
          ) : null}
          {error.code === "unavailable" || error.code === "quota" ? (
            <Link href="/profiles" className="underline">
              Use the form
            </Link>
          ) : null}
        </p>
      ) : null}

      <form
        onSubmit={(e) => {
          e.preventDefault();
          send(input);
        }}
        className="space-y-2"
      >
        <textarea
          dir="auto"
          rows={3}
          maxLength={4000}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send(input);
            }
          }}
          placeholder={messages.length === 0 ? "e.g. I work at … — or: tactical radios and night vision for armies" : "Say what to change…"}
          className={INPUT}
        />
        <div className="flex flex-wrap items-center gap-2">
          <Button type="submit" disabled={busy || !input.trim()}>
            Send
          </Button>
          {checkedCount > 0 ? (
            <Button type="button" variant="outline" disabled={saving || busy} onClick={create}>
              {saving ? "Saving…" : `Create ${checkedCount} ${checkedCount === 1 ? "profile" : "profiles"}`}
            </Button>
          ) : null}
          <button type="button" onClick={skip} className="text-sm text-slate-500 underline">
            Skip for now
          </button>
        </div>
      </form>
    </div>
  );
}
```

- [ ] **Step 7: Modify `web/app/page.tsx` — redirect to onboarding, and the "onboarded" notice**

Add imports at the top (next to the existing ones):
```ts
import { cookies } from "next/headers";

import { SKIP_COOKIE, shouldOnboard } from "@/lib/onboarding/gate";
```

Change the signature:
```ts
export default async function Page() {
```
to:
```ts
export default async function Page({ searchParams }: { searchParams: { onboarded?: string } }) {
```

Replace this block:
```ts
  // State 2 — signed out.
  if (!user) redirect("/login");

  const { tenders, matches, pipeline, counts } = await fetchWorkspace();

  // RLS makes this the honest test for membership: the policy on
  // `organizations` is `is_org_member(id)`, so a user in no organization gets
  // zero rows rather than an error.
  const { data: orgs } = await supabase.from("organizations").select("id").limit(1);
  const hasOrganization = (orgs?.length ?? 0) > 0;
```
with:
```ts
  // State 2 — signed out.
  if (!user) redirect("/login");

  // RLS makes this the honest test for membership: the policy on
  // `organizations` is `is_org_member(id)`, so a user in no organization gets
  // zero rows rather than an error.
  const { data: orgs } = await supabase.from("organizations").select("id").limit(1);
  const hasOrganization = (orgs?.length ?? 0) > 0;

  // A member with no alert profile has nothing to be alerted about: send them
  // to the onboarding chat first — unless they chose to skip it.
  if (hasOrganization) {
    const { data: anyProfile } = await supabase.from("alert_profiles").select("id").limit(1);
    const skipped = (await cookies()).get(SKIP_COOKIE)?.value === "1";
    if (shouldOnboard({ hasOrganization, hasProfile: (anyProfile?.length ?? 0) > 0, skipped })) {
      redirect("/onboarding");
    }
  }

  const { tenders, matches, pipeline, counts } = await fetchWorkspace();
```

Then, inside the returned `<Shell>`, directly before `{/* State 3 — signed in, but in no organization. */}`, add:
```tsx
      {searchParams.onboarded === "1" ? (
        <Notice title="Your alert profiles are saved.">
          Matching starts with tonight&apos;s run — new tenders that fit will appear here and in
          your email. You can change your profiles any time under Alert profiles.
        </Notice>
      ) : null}
```

- [ ] **Step 8: Modify `web/app/profiles/page.tsx` — the chat link**

Replace:
```tsx
        Describe what your company builds and bids on. Every new tender is compared against this
        description, and the ones that fit are sent to you by email and shown in your alerts.
      </p>
```
with:
```tsx
        Describe what your company builds and bids on. Every new tender is compared against this
        description, and the ones that fit are sent to you by email and shown in your alerts.{" "}
        <Link href="/onboarding" className="underline">
          Set up with chat
        </Link>
        .
      </p>
```

- [ ] **Step 9: Full checks**

Run (in `web/`): `npm test && npm run typecheck && npm run lint && npm run build`
Expected: all pass; the build lists `/onboarding`, `/api/onboarding` and `/api/onboarding/preview`.

- [ ] **Step 10: Smoke test without a key (fallback path)**

With `web/.env.local` containing only the Supabase variables (no `GEMINI_API_KEY`), run `npm run start -- -p 3125` after the build, sign in as a fresh test user created with the service role (as in earlier end-to-end tests), and confirm: `/` redirects to `/onboarding`; the page shows "Chat setup isn't available here" with the form link; "Skip for now" lands on `/` and a reload does not redirect again. Stop only the server on port 3125 (`Get-NetTCPConnection -LocalPort 3125` → `taskkill /PID <pid> /T /F`), never all `node.exe`. Delete the test user and its organization.

- [ ] **Step 11: Commit**

```bash
git add web/lib/onboarding/save.ts web/lib/onboarding/gate.ts web/app/onboarding web/components/onboarding-chat.tsx web/app/page.tsx web/app/profiles/page.tsx web/tests/onboarding/save.test.ts web/tests/onboarding/gate.test.ts
git commit -m "Onboarding: chat UI, saving approved drafts once, post-login redirect with skip

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Configure Gemini, verify live, document

Requires the user's Gemini API key. Do not start without it.

**Files:**
- Modify (gitignored, not committed): `web/.env.local`, `.env`
- Modify: `.env.example`, `HANDOFF.md`, `REPOMIX.md`
- Create (scratchpad, not committed): `embed_parity.py`, `web/scripts/embed-once.mts` (deleted after use)

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Configure both environments with the same embedding settings**

Append to `web/.env.local` and to the root `.env` (the model name for chat is the user's choice; use the same value as the scraper's `GEMINI_MODEL`):
```
GEMINI_API_KEY=<user's key>
GEMINI_MODEL=<chat/extraction model id>
EMBEDDING_PROVIDER=gemini
GEMINI_EMBEDDING_MODEL=gemini-embedding-001
EMBEDDING_DIM=1024
```
Root `.env` also needs `EXTRACTION_PROVIDER=gemini` for the sweep.

- [ ] **Step 2: Put tenders in the copy's database**

Run (repository root): `python scraper-service/run_sweep.py --source ted-eu --limit 300`
Expected: exit 0 or 1 (a narrow run is not an alarm), and the log line `DATABASE NOW HOLDS N TENDERS` with N > 0.

- [ ] **Step 3: Embedding parity (Python vs TypeScript)**

Create `web/scripts/embed-once.mts`:
```ts
import { readFileSync } from "node:fs";
import { createGemini } from "../lib/onboarding/gemini";
import { readConfig } from "../lib/onboarding/config";

for (const line of readFileSync(new URL("../.env.local", import.meta.url), "utf8").split(/\r?\n/)) {
  const m = line.match(/^([A-Z_]+)=(.*)$/);
  if (m && !process.env[m[1]]) process.env[m[1]] = m[2];
}
const v = await createGemini(readConfig()).embed(process.argv[2]);
console.log(JSON.stringify(v));
```
Create `embed_parity.py` in the scratchpad:
```python
import asyncio, json, subprocess, sys
sys.path.insert(0, r"C:\lumosource\tenders-finder\scraper-service")
from dotenv import load_dotenv; load_dotenv(r"C:\lumosource\tenders-finder\.env")
from app.config import Settings
from app.nlp.embeddings import get_embedding_provider

TEXT = "Tactical software-defined radios and encrypted battlefield communications for armed forces."
py = asyncio.run(get_embedding_provider(Settings()).embed([TEXT], input_type="query"))[0]
ts = json.loads(subprocess.check_output(["npx", "--yes", "tsx", "scripts/embed-once.mts", TEXT], cwd=r"C:\lumosource\tenders-finder\web", shell=True))
cos = sum(a * b for a, b in zip(py, ts))
print(f"dims py={len(py)} ts={len(ts)} cosine={cos:.6f}")
assert len(py) == len(ts) == 1024 and cos >= 0.999, "EMBEDDING PARITY FAILED"
print("EMBEDDING PARITY OK")
```
Run: `python <scratchpad>/embed_parity.py`
Expected: `dims py=1024 ts=1024 cosine=` ≥ 0.999, then `EMBEDDING PARITY OK`. Then delete `web/scripts/embed-once.mts`.

- [ ] **Step 4: Preview parity on real tenders**

Rerun `preview_parity.py` from Task 4 is synthetic; for real data, in the browser run of Step 5 save one profile, wait for the next `embed_pending_profiles` (run `python -c "import asyncio,sys; sys.path.insert(0,'scraper-service'); from dotenv import load_dotenv; load_dotenv('.env'); from app.pipeline import IngestionPipeline; print(asyncio.run(IngestionPipeline().embed_pending_profiles()))"`), then compare the count of `tender_matches` rows for that profile (service role, REST) with the preview total the card showed for the same description. Expected: equal. A small difference is only acceptable if a tender's status or deadline changed between the two reads; investigate any other difference before continuing.

- [ ] **Step 5: Live conversation checks in a browser** (`npm run build && npm run start -- -p 3125`, signed in as a fresh test user)

1. "I work at Elbit Systems" → several drafts, each with real source links, preview counts shown.
2. An invented name ("Zzyzx Quantum Defence Ltd") → "couldn't find it" and a question; no drafts.
3. Hebrew: "מכ"מים ואמצעי ראיית לילה לצבא" → a sensible draft, text shown right-to-left, and no research performed: in the browser's DevTools → Network, the `/api/onboarding` response for that turn has `"research": null` and the draft has `"sources": []`.
4. "only Europe and Israel" → the not-supported notice; drafts unchanged.
5. Approve two drafts → `/` shows the saved notice; `/profiles` shows both as "waiting for tonight's run".
6. Delete the test user and its organization; confirm 0 test organizations remain.

Stop only the server on port 3125.

- [ ] **Step 6: Document**

- `.env.example`: add a "Web (web/.env.local)" section listing `GEMINI_API_KEY`, `GEMINI_CHAT_MODEL`/`GEMINI_MODEL`, `EMBEDDING_PROVIDER`, `GEMINI_EMBEDDING_MODEL`, `EMBEDDING_DIM`, with the note that the embedding values must equal the root `.env`'s or the preview is meaningless.
- `HANDOFF.md`: in the copy section, record migration 0023 applied, the onboarding chat, and the two-places embedding risk with its guards.
- `REPOMIX.md`: add `ADR-30 — onboarding chat` (Hebrew, matching the file): two-call research/structure split and why links come from grounding metadata; preview mirrors the matcher in SQL; no geography; nothing stored before approval. Add a silent-failure row: "embedding made in two places | plausible preview numbers | different model or task type gives the right shape and a meaningless score — guarded by shared env names, a disabled-unless-matching check, and a parity test".

- [ ] **Step 7: Final checks and commit**

Run (in `web/`): `npm test && npm run typecheck && npm run lint && npm run build`
Run (in `scraper-service/`): `python -m pytest -q`
Expected: all pass.

```bash
git add .env.example HANDOFF.md REPOMIX.md
git commit -m "Document the onboarding chat: settings, migration 0023, ADR-30

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
