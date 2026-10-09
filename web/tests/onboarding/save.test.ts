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
