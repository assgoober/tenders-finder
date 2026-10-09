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

  it("is unavailable when the rpc call throws (network error)", async () => {
    const rpc: RpcFn = vi.fn(async () => {
      throw new Error("fetch failed");
    });
    const out = await previewDraft(DESC, [], {
      llm: llm(async () => VECTOR),
      rpc,
      config: CONFIG,
    });
    expect(out).toEqual({ status: "unavailable" });
  });

  it("is unavailable when the embedding service rejects the key", async () => {
    const out = await previewDraft(DESC, [], {
      llm: llm(async () => {
        throw new AgentError("unavailable");
      }),
      rpc: rpcReturning(null),
      config: CONFIG,
    });
    expect(out).toEqual({ status: "unavailable" });
  });

  it("is unavailable when the rpc result is missing open_tenders", async () => {
    const out = await previewDraft(DESC, [], {
      llm: llm(async () => VECTOR),
      rpc: rpcReturning({ total: 3, cut: 0.5, top: [] }),
      config: CONFIG,
    });
    expect(out).toEqual({ status: "unavailable" });
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

  it("does not propagate unavailable status from one draft to another", async () => {
    const unavailable: Preview = { status: "unavailable" };
    const compute = vi.fn(async (d: DraftView) => {
      return d.key === "a" ? unavailable : ok;
    });
    const out = await attachPreviews(
      [draft({ key: "a" }), draft({ key: "b" })],
      [],
      compute,
    );
    expect(out[0].preview).toEqual(unavailable);
    expect(out[1].preview).toEqual(ok);
  });
});
