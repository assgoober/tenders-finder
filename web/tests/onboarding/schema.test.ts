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
