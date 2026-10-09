import { describe, expect, it, vi } from "vitest";

import { AgentError, toAgentError } from "@/lib/onboarding/errors";
import { createGemini, type GenAIClient } from "@/lib/onboarding/gemini";
import { normalize } from "@/lib/onboarding/llm";
import type { OnboardingConfig } from "@/lib/onboarding/config";

const CONFIG: OnboardingConfig = {
  apiKey: "k",
  chatModel: "chat-model",
  embeddingProvider: "gemini",
  embeddingModel: "gemini-embedding-001",
  embeddingDim: 1024,
};

function client(response: unknown, embedding: number[] = [3, 4]) {
  const generateContent = vi.fn().mockResolvedValue(response);
  const embedContent = vi.fn().mockResolvedValue({ embeddings: [{ values: embedding }] });
  return {
    fake: { models: { generateContent, embedContent } } as unknown as GenAIClient,
    generateContent,
    embedContent,
  };
}

describe("per-call timeout", () => {
  it("passes the request's timeout to the SDK, and nothing when unset", async () => {
    const { fake, generateContent } = client({ text: "{}", candidates: [{ finishReason: "STOP" }] });
    const gemini = createGemini(CONFIG, fake);
    await gemini.generate({ system: "S", prompt: "P", timeoutMs: 12_000 });
    await gemini.generate({ system: "S", prompt: "P" });
    expect(generateContent.mock.calls[0][0].config.httpOptions).toEqual({ timeout: 12_000 });
    expect(generateContent.mock.calls[1][0].config.httpOptions).toBeUndefined();
  });
});

describe("generate", () => {
  it("turns on Google Search and returns grounding sources, deduplicated", async () => {
    const { fake, generateContent } = client({
      text: "Elbit makes radios.",
      candidates: [
        {
          finishReason: "STOP",
          groundingMetadata: {
            groundingChunks: [
              { web: { uri: "https://a.com/", title: "A" } },
              { web: { uri: "https://a.com/", title: "A" } },
              { web: { uri: "https://b.com/" } },
              {},
            ],
          },
        },
      ],
    });
    const out = await createGemini(CONFIG, fake).generate({ system: "S", prompt: "P", search: true });
    const req = generateContent.mock.calls[0][0];
    expect(req.model).toBe("chat-model");
    expect(req.config.tools).toEqual([{ googleSearch: {} }]);
    expect(req.config.systemInstruction).toBe("S");
    expect(req.config.responseJsonSchema).toBeUndefined();
    expect(out).toEqual({
      text: "Elbit makes radios.",
      sources: [
        { url: "https://a.com/", title: "A" },
        { url: "https://b.com/", title: "https://b.com/" },
      ],
      blocked: false,
    });
  });

  it("asks for JSON against the schema, without tools", async () => {
    const { fake, generateContent } = client({ text: "{}", candidates: [{ finishReason: "STOP" }] });
    await createGemini(CONFIG, fake).generate({ system: "S", prompt: "P", jsonSchema: { type: "object" } });
    const req = generateContent.mock.calls[0][0];
    expect(req.config.responseMimeType).toBe("application/json");
    expect(req.config.responseJsonSchema).toEqual({ type: "object" });
    expect(req.config.tools).toBeUndefined();
  });

  it("keeps only http(s) URIs in sources, numbered in first-seen order, and annotates text with citations", async () => {
    const { fake, generateContent } = client({
      text: "Radios are systems. Drones are aerial.",
      candidates: [
        {
          finishReason: "STOP",
          groundingMetadata: {
            groundingChunks: [
              { web: { uri: "https://a.com/", title: "A" } },
              { web: { uri: "https://a.com/", title: "A" } }, // Duplicate
              { web: { uri: "javascript:x" } }, // Not http(s)
              { web: { uri: "https://b.com/", title: "B" } },
            ],
            groundingSupports: [
              { segment: { endIndex: 19 }, groundingChunkIndices: [1] }, // After "systems." — cites duplicate chunk
              { segment: { endIndex: 38 }, groundingChunkIndices: [3] }, // After "aerial." — cites B
            ],
          },
        },
      ],
    });
    const out = await createGemini(CONFIG, fake).generate({ system: "S", prompt: "P", search: true });

    // Sources should be deduped and contain only http(s) URIs
    expect(out.sources).toEqual([
      { url: "https://a.com/", title: "A" },
      { url: "https://b.com/", title: "B" },
    ]);

    // Text should have citations inserted
    expect(out.text).toBe("Radios are systems.[1] Drones are aerial.[2]");
  });

  it.each([
    [{ promptFeedback: { blockReason: "SAFETY" }, candidates: [] }],
    [{ text: "", candidates: [{ finishReason: "SAFETY" }] }],
    [{ text: "", candidates: [{ finishReason: "PROHIBITED_CONTENT" }] }],
  ])("reports a safety block as blocked, not as an error", async (response) => {
    const { fake } = client(response);
    const out = await createGemini(CONFIG, fake).generate({ system: "S", prompt: "P" });
    expect(out.blocked).toBe(true);
  });

  it("maps SDK failures to agent errors", async () => {
    const { fake, generateContent } = client({});
    generateContent.mockRejectedValueOnce(Object.assign(new Error("Too many"), { status: 429 }));
    await expect(createGemini(CONFIG, fake).generate({ system: "S", prompt: "P" })).rejects.toMatchObject({
      code: "quota",
    });
  });

  it("rejects when both search and jsonSchema are provided", async () => {
    const { fake, generateContent } = client({});
    await expect(
      createGemini(CONFIG, fake).generate({ system: "S", prompt: "P", search: true, jsonSchema: { type: "object" } }),
    ).rejects.toMatchObject({
      code: "upstream",
      message: "search and jsonSchema cannot be combined in one call",
    });
    expect(generateContent).not.toHaveBeenCalled();
  });
});

describe("embed", () => {
  it("uses the sweep's recipe and normalizes", async () => {
    const { fake, embedContent } = client({}, [3, 4]);
    const v = await createGemini(CONFIG, fake).embed("radios");
    const req = embedContent.mock.calls[0][0];
    expect(req.model).toBe("gemini-embedding-001");
    expect(req.contents).toEqual(["radios"]);
    expect(req.config).toEqual({ taskType: "RETRIEVAL_QUERY", outputDimensionality: 1024 });
    expect(v).toEqual([0.6, 0.8]);
  });

  it("throws when the response contains no embedding values", async () => {
    const { fake } = client({}, []);
    fake.models.embedContent = vi.fn().mockResolvedValue({ embeddings: [] });
    await expect(createGemini(CONFIG, fake).embed("radios")).rejects.toMatchObject({
      code: "upstream",
      message: "the embedding response contained no vector",
    });
  });
});

describe("normalize", () => {
  it("scales to unit length and leaves a zero vector alone", () => {
    expect(normalize([3, 4])).toEqual([0.6, 0.8]);
    expect(normalize([0, 0])).toEqual([0, 0]);
  });
});

describe("annotateCitations", () => {
  it("inserts citation labels at byte offsets in ASCII text", async () => {
    const { annotateCitations } = await import("@/lib/onboarding/gemini");
    const text = "Elbit makes radios. They make drones.";
    const supports = [
      { segment: { endIndex: 19 }, groundingChunkIndices: [0] }, // After "radios."
      { segment: { endIndex: 37 }, groundingChunkIndices: [1] }, // After "drones."
    ];
    const chunkNumber = new Map([[0, 1], [1, 2]]);

    const result = annotateCitations(text, supports, chunkNumber);
    expect(result).toBe("Elbit makes radios.[1] They make drones.[2]");
  });

  it("merges labels that share the same endIndex", async () => {
    const { annotateCitations } = await import("@/lib/onboarding/gemini");
    const text = "Elbit makes radios.";
    const supports = [
      { segment: { endIndex: 19 }, groundingChunkIndices: [0, 1] },
    ];
    const chunkNumber = new Map([[0, 1], [1, 2]]);

    const result = annotateCitations(text, supports, chunkNumber);
    expect(result).toBe("Elbit makes radios.[1][2]");
  });

  it("skips unknown chunk indices", async () => {
    const { annotateCitations } = await import("@/lib/onboarding/gemini");
    const text = "Elbit makes radios.";
    const supports = [
      { segment: { endIndex: 19 }, groundingChunkIndices: [0, 99] },
    ];
    const chunkNumber = new Map([[0, 1]]);

    const result = annotateCitations(text, supports, chunkNumber);
    expect(result).toBe("Elbit makes radios.[1]");
  });

  it("handles Hebrew text with byte-offset endIndex", async () => {
    const { annotateCitations } = await import("@/lib/onboarding/gemini");
    const text = "רדיו טקטי. מל\"טים.";
    const endIndex = Buffer.byteLength("רדיו טקטי.", "utf8");
    const supports = [
      { segment: { endIndex }, groundingChunkIndices: [0] },
    ];
    const chunkNumber = new Map([[0, 1]]);

    const result = annotateCitations(text, supports, chunkNumber);
    expect(result.startsWith("רדיו טקטי.[1]")).toBe(true);
  });

  it("returns text unchanged when there are no supports", async () => {
    const { annotateCitations } = await import("@/lib/onboarding/gemini");
    const text = "Some text";
    const result = annotateCitations(text, [], new Map());
    expect(result).toBe("Some text");
  });
});

describe("toAgentError", () => {
  it.each([
    [Object.assign(new Error("x"), { status: 429 }), "quota"],
    [new Error("RESOURCE_EXHAUSTED: quota"), "quota"],
    [Object.assign(new Error("aborted"), { name: "AbortError" }), "timeout"],
    [new Error("request timed out"), "timeout"],
    [Object.assign(new Error("x"), { status: 403 }), "unavailable"],
    [new Error("API key not valid"), "unavailable"],
    [new Error("socket hang up"), "upstream"],
  ])("maps %s to %s", (err, code) => {
    expect(toAgentError(err).code).toBe(code);
  });
  it("passes an AgentError through", () => {
    const e = new AgentError("cap");
    expect(toAgentError(e)).toBe(e);
  });
});
