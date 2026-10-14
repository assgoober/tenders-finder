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
