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
    const view = toDraftView(draft({ source_ids: [2] }), research, "k1", true);
    expect(view.sources.map((s) => s.url)).toEqual(["https://example.org/radios"]);
    expect(view.unconfirmed).toBe(false);
  });
  it("flags a draft with no citation as unconfirmed", () => {
    expect(toDraftView(draft(), research, "k1", true).unconfirmed).toBe(true);
  });
  it("flags a draft citing only numbers that do not exist", () => {
    const view = toDraftView(draft({ source_ids: [9, 0, -1] }), research, "k1", true);
    expect(view.sources).toEqual([]);
    expect(view.unconfirmed).toBe(true);
  });
  it("does not let the model clear the flag on the turn that researched", () => {
    expect(toDraftView(draft({ from_user: true }), research, "k1", true).unconfirmed).toBe(true);
  });
  it("honours from_user when the research is carried over from an earlier turn", () => {
    expect(toDraftView(draft({ from_user: true }), research, "k1", false).unconfirmed).toBe(false);
    expect(toDraftView(draft(), research, "k1", false).unconfirmed).toBe(true);
  });
  it("does not flag drafts when no research happened (direct description)", () => {
    const view = toDraftView(draft(), null, "k1", false);
    expect(view).toMatchObject({ sources: [], unconfirmed: false, preview: null, key: "k1" });
  });
  it("renames snake_case fields for the client", () => {
    const view = toDraftView(draft({ excluded_keywords: ["catering"] }), null, "k1", false);
    expect(view.excludedKeywords).toEqual(["catering"]);
  });
});
