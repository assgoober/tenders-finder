import { describe, expect, it } from "vitest";

import { GEO_NOTICE, runTurn } from "@/lib/onboarding/agent";
import { GEO_NOTICE_TEXT_HE, BLOCKED_TEXT_HE } from "@/lib/onboarding/prompts";
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

describe("citation state preservation during refinement", () => {
  it("includes source_ids and from_user in the prompt for existing drafts", async () => {
    const existing: DraftView[] = [
      {
        key: "d1",
        name: "Radios",
        description: DESC_A,
        keywords: ["SDR"],
        excludedKeywords: [],
        sources: [{ n: 1, url: "https://elbitsystems.com/", title: "Elbit" }],
        unconfirmed: false,
        preview: null,
      },
    ];
    const research = { query: "Elbit", text: "Elbit makes radios.", sources: existing[0].sources };
    const { llm, calls } = fake([classify("refine"), turn()]);
    await runTurn(request("drop simulators", { drafts: existing, research }), llm, key);

    // The drafting prompt should include source_ids and from_user
    expect(calls[1].prompt).toContain('"source_ids": [');
    expect(calls[1].prompt).toMatch(/"source_ids":\s*\[\s*1\s*\]/);
    expect(calls[1].prompt).toContain('"from_user": false');
  });

  it("preserves the key of a prior draft with the same name", async () => {
    const existing: DraftView[] = [
      {
        key: "d1",
        name: "Radios",
        description: DESC_A,
        keywords: ["SDR"],
        excludedKeywords: [],
        sources: [{ n: 1, url: "https://elbitsystems.com/", title: "Elbit" }],
        unconfirmed: false,
        preview: null,
      },
    ];
    const research = { query: "Elbit", text: "Elbit makes radios.", sources: existing[0].sources };
    const { llm } = fake([classify("refine"), turn({ drafts: [modelDraft({ source_ids: [] })] })]);
    const out = await runTurn(request("drop simulators", { drafts: existing, research }), llm, key);

    expect(out.drafts[0].key).toBe("d1");
  });

  it("generates a fresh key for a new draft with a different name", async () => {
    n = 0; // Reset counter
    const existing: DraftView[] = [
      {
        key: "d1",
        name: "Radios",
        description: DESC_A,
        keywords: ["SDR"],
        excludedKeywords: [],
        sources: [{ n: 1, url: "https://elbitsystems.com/", title: "Elbit" }],
        unconfirmed: false,
        preview: null,
      },
    ];
    const research = { query: "Elbit", text: "Elbit makes radios.", sources: existing[0].sources };
    const { llm } = fake([
      classify("refine"),
      turn({
        drafts: [
          modelDraft({ source_ids: [] }),
          modelDraft({ name: "UAV", description: DESC_B, source_ids: [] }),
        ],
      }),
    ]);
    const out = await runTurn(request("also add UAVs", { drafts: existing, research }), llm, key);

    // First draft keeps existing key
    expect(out.drafts[0].key).toBe("d1");
    // Second draft gets a new key
    expect(out.drafts[1].key).toBe("k1");
  });
});

describe("language-aware notices", () => {
  it("responds with Hebrew geography notice for a Hebrew message", async () => {
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
    const { llm, calls } = fake([classify("geography_only", null, true)]);
    const out = await runTurn(request("רק אירופה וישראל", { drafts: existing }), llm, key);

    expect(out.reply).toBe(GEO_NOTICE_TEXT_HE);
    expect(out.drafts).toEqual(existing);
  });

  it("responds with Hebrew blocked message when research is blocked for a Hebrew message", async () => {
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
    const { llm, calls } = fake([classify("company", "חברה"), { blocked: true }]);
    const out = await runTurn(request("חברה כלשהי", { drafts: existing }), llm, key);

    expect(out.kind).toBe("clarify");
    expect(out.reply).toBe(BLOCKED_TEXT_HE);
    expect(out.drafts).toEqual(existing);
  });

  it("prefixes Hebrew geography notice when geography comes with a real correction", async () => {
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
    const { llm } = fake([
      classify("refine", null, true),
      turn({ reply: "הוספתי סימולטורים." }),
    ]);
    const out = await runTurn(request("הסר סימולטורים, רק אירופה", { drafts: existing }), llm, key);

    expect(out.reply.startsWith(GEO_NOTICE_TEXT_HE)).toBe(true);
    expect(out.reply).toContain("הוספתי סימולטורים.");
  });
});
