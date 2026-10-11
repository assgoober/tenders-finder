import { describe, expect, it, vi } from "vitest";

import { GEO_NOTICE, runTurn, structured } from "@/lib/onboarding/agent";
import {
  BLOCKED_DRAFT_TEXT_HE,
  BLOCKED_TEXT_HE,
  GEO_NOTICE_NO_DRAFTS_TEXT,
  GEO_NOTICE_NO_DRAFTS_TEXT_HE,
  GEO_NOTICE_TEXT_HE,
} from "@/lib/onboarding/prompts";
import { AgentError, USER_MESSAGES } from "@/lib/onboarding/errors";
import type { GeminiLike, GenerateRequest, GenerateResult } from "@/lib/onboarding/llm";
import { Classification, type DraftView, type TurnRequest } from "@/lib/onboarding/schema";

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

describe("the model cannot vouch for itself on the researching turn", () => {
  it("keeps a draft with no citations unconfirmed even if the model says from_user", async () => {
    const { llm } = fake([
      classify("company", "Elbit Systems"),
      { text: "Elbit makes radios.", sources: [{ url: "https://elbitsystems.com/", title: "Elbit" }] },
      turn({ drafts: [modelDraft({ source_ids: [], from_user: true })] }),
    ]);
    const out = await runTurn(request("I work at Elbit Systems"), llm, key);
    expect(out.drafts[0].unconfirmed).toBe(true);
  });

  it("honours from_user on a refine turn with carried research", async () => {
    const research = { query: "Elbit Systems", text: "Elbit makes radios.", sources: [] };
    const { llm } = fake([classify("refine"), turn({ drafts: [modelDraft({ source_ids: [], from_user: true })] })]);
    const out = await runTurn(request("also night vision", { research }), llm, key);
    expect(out.drafts[0].unconfirmed).toBe(false);
  });
});

describe("a different company invalidates old citations", () => {
  it("sends existing drafts with no citations when the research changes", async () => {
    const existing: DraftView[] = [
      {
        key: "d1",
        name: "Radios",
        description: DESC_A,
        keywords: [],
        excludedKeywords: [],
        sources: [{ n: 1, url: "https://elbitsystems.com/", title: "Elbit" }],
        unconfirmed: false,
        preview: null,
      },
    ];
    const research = { query: "Elbit", text: "Elbit makes radios.", sources: existing[0].sources };
    const { llm, calls } = fake([
      classify("company", "Rafael"),
      { text: "Rafael makes missiles.", sources: [{ url: "https://rafael.co.il/", title: "Rafael" }] },
      turn(),
    ]);
    await runTurn(request("actually Rafael", { drafts: existing, research }), llm, key);
    expect(calls[2].prompt).toMatch(/"source_ids":\s*\[\s*\]/);
    expect(calls[2].prompt).toContain('"from_user": false');
    expect(calls[2].prompt).not.toMatch(/"source_ids":\s*\[\s*1\s*\]/);
  });
});

describe("duplicate draft names", () => {
  it("reuses a prior key at most once", async () => {
    n = 0;
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
      classify("refine"),
      turn({ drafts: [modelDraft({ source_ids: [] }), modelDraft({ source_ids: [], description: DESC_B })] }),
    ]);
    const out = await runTurn(request("x", { drafts: existing }), llm, key);
    expect(out.drafts.map((d) => d.key)).toEqual(["d1", "k1"]);
  });
});

describe("research through a separate researcher (OpenAI)", () => {
  it("uses the researcher for the company lookup and Gemini for everything else", async () => {
    const { llm, calls } = fake([classify("company", "Elbit Systems"), turn()]);
    const research = vi.fn(async (_req: { system: string; prompt: string; timeoutMs?: number }) => ({
      text: "Elbit makes radios. [1]",
      sources: [{ url: "https://elbitsystems.com/", title: "Elbit Systems" }],
      blocked: false,
    }));
    const out = await runTurn(request("I work at Elbit Systems"), llm, key, Date.now() + 50_000, { research });
    expect(research).toHaveBeenCalledTimes(1);
    expect(research.mock.calls[0][0]).toMatchObject({ prompt: expect.stringContaining("Elbit Systems") });
    expect(research.mock.calls[0][0].timeoutMs).toBeGreaterThan(0);
    expect(calls.some((c) => c.search)).toBe(false);
    expect(calls).toHaveLength(2);
    expect(out.research?.sources[0]).toMatchObject({ n: 1, url: "https://elbitsystems.com/" });
    expect(out.drafts[0].unconfirmed).toBe(false);
  });

  it("treats a researcher refusal like a blocked lookup", async () => {
    const { llm } = fake([classify("company", "Something")]);
    const research = vi.fn(async (_req: { system: string; prompt: string; timeoutMs?: number }) => ({ text: "", sources: [], blocked: true }));
    const out = await runTurn(request("Something"), llm, key, undefined, { research });
    expect(out.kind).toBe("clarify");
    expect(out.reply).toBe(USER_MESSAGES.blocked);
  });
});

describe("the schema sent to Gemini", () => {
  it("has no minItems/maxItems, which the Gemini API rejects with 400 INVALID_ARGUMENT", async () => {
    // Found in the live check: the drafting schema was refused outright, so
    // every turn failed with "something went wrong". Array sizes are still
    // enforced after the response (sanitizeModelTurn + Zod).
    const { llm, calls } = fake([classify("describe"), turn()]);
    await runTurn(request("radios"), llm, key);
    for (const c of calls) {
      const sent = JSON.stringify(c.jsonSchema);
      expect(sent).not.toMatch(/"minItems"|"maxItems"/);
    }
  });
  it("still rejects an over-long array after the response", async () => {
    const tooMany = Array.from({ length: 9 }, (_, i) => modelDraft({ name: `P${i}`, source_ids: [], from_user: true }));
    const { llm } = fake([classify("describe"), turn({ drafts: tooMany })]);
    const out = await runTurn(request("radios"), llm, key);
    expect(out.drafts.length).toBeLessThanOrEqual(8);
  });
});

describe("deadline", () => {
  it("does not retry a bad answer when little time is left", async () => {
    const { llm, calls } = fake([classify("describe"), { text: "not json" }, turn()]);
    await expect(runTurn(request("radios"), llm, key, Date.now() + 12_000)).rejects.toMatchObject({
      code: "timeout",
    });
    expect(calls).toHaveLength(2);
  });
  it("does not start research with under 10s left", async () => {
    const { llm, calls } = fake([classify("company", "Elbit Systems"), turn()]);
    await expect(runTurn(request("Elbit Systems"), llm, key, Date.now() + 5_000)).rejects.toMatchObject({
      code: "timeout",
    });
    expect(calls).toHaveLength(1);
  });
  it("does not start the structuring call with under 10s left", async () => {
    const { llm, calls } = fake([classify("describe"), turn()]);
    await expect(runTurn(request("radios"), llm, key, Date.now() + 5_000)).rejects.toMatchObject({
      code: "timeout",
    });
    expect(calls).toHaveLength(1);
  });
  it("gives each model call a timeout that fits inside the time left", async () => {
    // A call allowed the adapter's full 45s could still overrun the route's
    // 60s limit when it starts late in the turn.
    const { llm, calls } = fake([classify("describe"), turn()]);
    await runTurn(request("radios"), llm, key, Date.now() + 30_000);
    expect(calls).toHaveLength(2);
    for (const c of calls) {
      expect(c.timeoutMs).toBeGreaterThan(0);
      expect(c.timeoutMs!).toBeLessThanOrEqual(28_000);
    }
  });
  it("never asks for more than the adapter's 45s per call", async () => {
    const { llm, calls } = fake([classify("describe"), turn()]);
    await runTurn(request("radios"), llm, key, Date.now() + 120_000);
    for (const c of calls) expect(c.timeoutMs).toBe(45_000);
  });
  it("leaves the per-call timeout to the adapter when there is no deadline", async () => {
    const { llm, calls } = fake([classify("describe"), turn()]);
    await runTurn(request("radios"), llm, key);
    for (const c of calls) expect(c.timeoutMs).toBeUndefined();
  });
  it("runs normally with plenty of time", async () => {
    const { llm } = fake([classify("describe"), turn()]);
    const out = await runTurn(request("radios"), llm, key, Date.now() + 50_000);
    expect(out.kind).toBe("drafts");
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

describe("polish", () => {
  const oneDraft: DraftView[] = [
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

  it("treats the same company with different spacing and case as already researched", async () => {
    const research = { query: "Elbit Systems", text: "Elbit makes radios.", sources: [] };
    const { llm, calls } = fake([classify("company", "  elbit   systems "), turn()]);
    await runTurn(request("elbit systems", { research }), llm, key);
    expect(calls.some((c) => c.search)).toBe(false);
  });

  it("says it cannot draft (not 'look that up') when the structuring call is blocked", async () => {
    const { llm } = fake([classify("describe"), { blocked: true }]);
    const out = await runTurn(request("radios"), llm, key);
    expect(out.kind).toBe("clarify");
    expect(out.reply).toBe(USER_MESSAGES.blocked_draft);
    expect(out.reply).not.toBe(USER_MESSAGES.blocked);
  });

  it("says it cannot draft in Hebrew when the structuring call is blocked", async () => {
    const { llm } = fake([classify("describe"), { blocked: true }]);
    const out = await runTurn(request("רדיו טקטי"), llm, key);
    expect(out.reply).toBe(BLOCKED_DRAFT_TEXT_HE);
  });

  it("keeps the research wording when the research call is blocked", async () => {
    const { llm } = fake([classify("company", "Something"), { blocked: true }]);
    const out = await runTurn(request("Something"), llm, key);
    expect(out.reply).toBe(USER_MESSAGES.blocked);
  });

  it("retries an invalid classification once, feeding the problem back", async () => {
    const { llm, calls } = fake([{ text: "not json" }, classify("describe"), turn()]);
    const out = await runTurn(request("radios"), llm, key);
    expect(out.kind).toBe("drafts");
    expect(calls[1].prompt).toContain("was rejected");
  });

  it("does not claim to have left profiles unchanged when there are none (English)", async () => {
    const { llm } = fake([classify("geography_only", null, true)]);
    const out = await runTurn(request("only Europe"), llm, key);
    expect(out.reply).toBe(GEO_NOTICE_NO_DRAFTS_TEXT);
    expect(out.reply).not.toContain("unchanged");
  });

  it("does not claim to have left profiles unchanged when there are none (Hebrew)", async () => {
    const { llm } = fake([classify("geography_only", null, true)]);
    const out = await runTurn(request("רק אירופה"), llm, key);
    expect(out.reply).toBe(GEO_NOTICE_NO_DRAFTS_TEXT_HE);
  });

  it("keeps the 'unchanged' notice when drafts exist", async () => {
    const { llm } = fake([classify("geography_only", null, true)]);
    const out = await runTurn(request("only Europe", { drafts: oneDraft }), llm, key);
    expect(out.reply).toBe(GEO_NOTICE);
  });

  it("labels a clean() failure as 'could not be processed', not as bad JSON", async () => {
    const { llm, calls } = fake([classify("describe"), classify("describe")]);
    let first = true;
    const out = await structured(llm, "S", "P", Classification, (raw) => {
      if (first) {
        first = false;
        throw new Error("boom");
      }
      return raw;
    });
    expect(out.intent).toBe("describe");
    expect(calls[1].prompt).toContain("could not be processed (boom)");
    expect(calls[1].prompt).not.toContain("not valid JSON");
  });

  it("still labels a JSON.parse failure as 'not valid JSON'", async () => {
    const { llm, calls } = fake([{ text: "nope" }, classify("describe")]);
    await structured(llm, "S", "P", Classification);
    expect(calls[1].prompt).toContain("not valid JSON");
  });

  it("keeps every cited source's marker when a long research text is cut", async () => {
    const filler = "word ".repeat(4100); // ~20500 chars
    const text = `Start [1]. ${filler}end [2][3]`;
    const { llm } = fake([
      classify("company", "Elbit Systems"),
      {
        text,
        sources: [
          { url: "https://a.com/", title: "A" },
          { url: "https://b.com/", title: "B" },
          { url: "https://c.com/", title: "C" },
        ],
      },
      turn(),
    ]);
    const out = await runTurn(request("Elbit Systems"), llm, key);
    const kept = out.research!.text;
    expect(kept.length).toBeLessThanOrEqual(20000);
    expect(kept).toContain("[1]");
    expect(kept.endsWith("\u2026 [2][3]")).toBe(true);
  });
});
