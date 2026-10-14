import { describe, expect, it } from "vitest";

import {
  descriptionProblem,
  isStaleResponse,
  needsPreviewRefresh,
  type PreviewCard,
} from "@/lib/onboarding/preview-state";

const DESC = "Tactical software-defined radios for armed forces and border police.";
const card = (over: Partial<PreviewCard> = {}): PreviewCard => ({
  description: DESC,
  excludedKeywords: [],
  previewInput: { description: DESC, excludedKeywords: [] },
  ...over,
});

describe("needsPreviewRefresh", () => {
  it("is false when the preview was computed for exactly this input", () => {
    expect(needsPreviewRefresh(card())).toBe(false);
  });
  it("is true when the description was edited", () => {
    expect(needsPreviewRefresh(card({ description: DESC + " And more." }))).toBe(true);
  });
  it("is true when the exclusions differ", () => {
    expect(needsPreviewRefresh(card({ excludedKeywords: ["catering"] }))).toBe(true);
  });
  it("is true when no preview was ever computed", () => {
    expect(needsPreviewRefresh(card({ previewInput: null }))).toBe(true);
  });
  it("is false for a description the API would refuse", () => {
    expect(needsPreviewRefresh(card({ description: "radios", previewInput: null }))).toBe(false);
    expect(needsPreviewRefresh(card({ description: "x".repeat(4001), previewInput: null }))).toBe(false);
  });
});

describe("isStaleResponse", () => {
  it("is false when the draft still holds what was sent", () => {
    expect(isStaleResponse(card(), DESC, [])).toBe(false);
  });
  it("is true when the description changed meanwhile", () => {
    expect(isStaleResponse(card({ description: DESC + "!" }), DESC, [])).toBe(true);
  });
  it("is true when the exclusions changed meanwhile", () => {
    expect(isStaleResponse(card({ excludedKeywords: ["a"] }), DESC, [])).toBe(true);
  });
  it("is true when the draft is gone", () => {
    expect(isStaleResponse(undefined, DESC, [])).toBe(true);
  });
});

describe("descriptionProblem", () => {
  it("passes 40 to 4000 trimmed characters", () => {
    expect(descriptionProblem(DESC)).toBeNull();
    expect(descriptionProblem("x".repeat(40))).toBeNull();
    expect(descriptionProblem("x".repeat(4000))).toBeNull();
  });
  it("names the problem otherwise", () => {
    expect(descriptionProblem("short")).toBe("At least 40 characters");
    expect(descriptionProblem(`  ${"x".repeat(39)}  `)).toBe("At least 40 characters");
    expect(descriptionProblem("x".repeat(4001))).toBe("At most 4,000 characters");
  });
});
