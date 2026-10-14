import { describe, expect, it } from "vitest";

import {
  MAX_CANDIDATES,
  MAX_KEYWORDS,
  TurnRequest,
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
    ["keyword with tab", { keywords: ["a\tb"] }],
    ["keyword with carriage return", { keywords: ["a\rb"] }],
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

describe("source URLs are http(s) only", () => {
  const base = {
    messages: [{ role: "user", text: "hi" }],
    drafts: [],
  };
  const research = (url: string) => ({
    query: "q",
    text: "t",
    sources: [{ n: 1, url, title: "x" }],
  });
  it("rejects a javascript: source in research", () => {
    expect(TurnRequest.safeParse({ ...base, research: research("javascript:alert(1)") }).success).toBe(false);
  });
  it("accepts an https source", () => {
    expect(TurnRequest.safeParse({ ...base, research: research("https://a.com/") }).success).toBe(true);
  });
});

describe("source_ids are cleaned", () => {
  it("keeps integers only, dedupes and caps at 20", () => {
    const ids = [1, 1, 2.5, "3", 4, ...Array.from({ length: 25 }, (_, i) => i + 100)];
    const raw = { kind: "drafts", reply: "ok then", candidates: [], drafts: [draft({ source_ids: ids })] };
    const parsed = ModelTurn.safeParse(sanitizeModelTurn(raw));
    expect(parsed.success).toBe(true);
    const out = parsed.success ? parsed.data.drafts[0].source_ids : [];
    expect(out.slice(0, 3)).toEqual([1, 4, 100]);
    expect(out.length).toBe(20);
    expect(new Set(out).size).toBe(out.length);
    expect(out.every(Number.isInteger)).toBe(true);
  });
});

describe("polish: tabs, carriage returns and the candidate cap", () => {
  it("turns tabs and carriage returns inside a keyword into spaces", () => {
    expect(sanitizeKeywords(["a\tb", "c\rd"])).toEqual(["a b", "c d"]);
  });

  it("caps a 12-candidate turn at MAX_CANDIDATES", () => {
    const candidates = Array.from({ length: 12 }, (_, i) => ({ name: `Co ${i}`, country: null, website: null }));
    const out = sanitizeModelTurn({ kind: "candidates", reply: "Which?", candidates, drafts: [] }) as {
      candidates: unknown[];
    };
    expect(out.candidates).toHaveLength(MAX_CANDIDATES);
    expect(ModelTurn.safeParse(out).success).toBe(true);
  });
});
