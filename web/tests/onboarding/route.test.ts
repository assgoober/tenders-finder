import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  user: { id: "u1" } as { id: string } | null,
  orgs: [{ id: "o1" }] as { id: string }[] | null,
  orgsError: null as { message: string } | null,
  runTurn: vi.fn(),
  previewDraft: vi.fn(),
  times: [] as number[],
  times_last: 0,
}));

vi.mock("@/lib/supabase/server", () => ({
  createClient: async () => ({
    auth: { getUser: async () => ({ data: { user: mocks.user } }) },
    from: () => ({ select: () => ({ limit: async () => ({ data: mocks.orgs, error: mocks.orgsError }) }) }),
    rpc: async () => ({ data: null, error: null }),
  }),
}));
vi.mock("@/lib/onboarding/clock", () => ({ clock: { now: () => mocks.times.shift() ?? mocks.times_last } }));
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
  mocks.orgsError = null;
  mocks.runTurn.mockReset();
  mocks.previewDraft.mockReset();
  mocks.times = [];
  mocks.times_last = 0;
  vi.stubEnv("GEMINI_API_KEY", "k");
  vi.stubEnv("GEMINI_CHAT_MODEL", "m");
});

afterEach(() => {
  vi.unstubAllEnvs();
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

  const oneDraftTurn = () => ({
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

  it("gives runTurn a deadline 50s ahead", async () => {
    mocks.times = [1_000_000];
    mocks.times_last = 1_000_000;
    mocks.runTurn.mockResolvedValue({ ...oneDraftTurn(), drafts: [] });
    await post(POST, body());
    expect(mocks.runTurn.mock.calls[0][3]).toBe(1_050_000);
  });

  it("skips previews when the turn used up the time budget", async () => {
    mocks.times = [1_000_000, 1_045_000];
    mocks.runTurn.mockResolvedValue(oneDraftTurn());
    const res = await post(POST, body());
    expect(res.status).toBe(200);
    expect((await res.json()).drafts[0].preview).toEqual({ status: "unavailable" });
    expect(mocks.previewDraft).not.toHaveBeenCalled();
  });

  it("returns 504 when the agent times out", async () => {
    mocks.runTurn.mockRejectedValue(new AgentError("timeout"));
    expect((await post(POST, body())).status).toBe(504);
  });

  it("returns 502 upstream when organizations query returns an error", async () => {
    mocks.orgsError = { message: "boom" };
    const res = await post(POST, body());
    expect(res.status).toBe(502);
    expect((await res.json()).error.code).toBe("upstream");
  });
});

describe("POST /api/onboarding/preview", () => {
  it("refuses a signed-out user", async () => {
    mocks.user = null;
    expect((await post(PREVIEW, { description: DESC, excludedKeywords: [] })).status).toBe(401);
  });

  it("refuses a user with no organization", async () => {
    mocks.orgs = [];
    expect((await post(PREVIEW, { description: DESC, excludedKeywords: [] })).status).toBe(403);
  });

  it("says chat is unavailable when no key is configured", async () => {
    vi.stubEnv("GEMINI_API_KEY", "");
    const res = await post(PREVIEW, { description: DESC, excludedKeywords: [] });
    expect(res.status).toBe(503);
    expect((await res.json()).error.code).toBe("unavailable");
  });

  it("rejects a description the form would refuse", async () => {
    expect((await post(PREVIEW, { description: "radios", excludedKeywords: [] })).status).toBe(400);
  });

  it("rejects an exclusion keyword containing %", async () => {
    expect((await post(PREVIEW, { description: DESC, excludedKeywords: ["bad%keyword"] })).status).toBe(400);
  });

  it("returns the preview", async () => {
    mocks.previewDraft.mockResolvedValue({ status: "unavailable" });
    const res = await post(PREVIEW, { description: DESC, excludedKeywords: ["catering"] });
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ preview: { status: "unavailable" } });
    expect(mocks.previewDraft.mock.calls[0][1]).toEqual(["catering"]);
  });

  it("wraps previewDraft errors in error shape with status 502", async () => {
    mocks.previewDraft.mockRejectedValue(new Error("network fail"));
    const res = await post(PREVIEW, { description: DESC, excludedKeywords: [] });
    expect(res.status).toBe(502);
    const json = await res.json();
    expect(json).toEqual({ error: { code: "upstream", message: expect.any(String) } });
  });
});
