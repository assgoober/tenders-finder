import { describe, expect, it } from "vitest";

import {
  chatUnavailableReason,
  previewUnavailableReason,
  readConfig,
  researchProvider,
} from "@/lib/onboarding/config";

const FULL = {
  GEMINI_API_KEY: "k",
  GEMINI_CHAT_MODEL: "chat-model",
  EMBEDDING_PROVIDER: "gemini",
  GEMINI_EMBEDDING_MODEL: "gemini-embedding-001",
  EMBEDDING_DIM: "1024",
};

describe("config", () => {
  it("reads every setting", () => {
    expect(readConfig(FULL)).toEqual({
      apiKey: "k",
      chatModel: "chat-model",
      embeddingProvider: "gemini",
      embeddingModel: "gemini-embedding-001",
      embeddingDim: 1024,
      _embeddingDimRaw: "1024",
      openaiApiKey: null,
      openaiResearchModel: null,
    });
  });
  it("uses OpenAI for research only when both its key and model are set", () => {
    expect(researchProvider(readConfig(FULL))).toBe("gemini");
    expect(researchProvider(readConfig({ ...FULL, OPENAI_API_KEY: "sk-x" }))).toBe("gemini");
    expect(researchProvider(readConfig({ ...FULL, OPENAI_RESEARCH_MODEL: "m" }))).toBe("gemini");
    const both = readConfig({ ...FULL, OPENAI_API_KEY: "sk-x", OPENAI_RESEARCH_MODEL: "m" });
    expect(researchProvider(both)).toBe("openai");
    expect(both).toMatchObject({ openaiApiKey: "sk-x", openaiResearchModel: "m" });
  });
  it("falls back to GEMINI_MODEL for chat and to 1024 dimensions", () => {
    const c = readConfig({ GEMINI_API_KEY: "k", GEMINI_MODEL: "m" });
    expect(c.chatModel).toBe("m");
    expect(c.embeddingDim).toBe(1024);
  });
  it("says why chat is unavailable", () => {
    expect(chatUnavailableReason(readConfig({}))).toMatch(/GEMINI_API_KEY/);
    expect(chatUnavailableReason(readConfig({ GEMINI_API_KEY: "k" }))).toMatch(/GEMINI_CHAT_MODEL/);
    expect(chatUnavailableReason(readConfig(FULL))).toBeNull();
  });
  it("disables the preview unless the embedding setup matches the sweep's", () => {
    expect(previewUnavailableReason(readConfig(FULL))).toBeNull();
    expect(previewUnavailableReason(readConfig({ ...FULL, EMBEDDING_PROVIDER: "voyage" }))).toMatch(
      /EMBEDDING_PROVIDER/,
    );
    expect(
      previewUnavailableReason(readConfig({ ...FULL, GEMINI_EMBEDDING_MODEL: "" })),
    ).toMatch(/GEMINI_EMBEDDING_MODEL/);
    expect(previewUnavailableReason(readConfig({ ...FULL, EMBEDDING_DIM: "768" }))).toMatch(/1024/);
  });
  it("detects malformed EMBEDDING_DIM and names the problem", () => {
    const reason = previewUnavailableReason(readConfig({ ...FULL, EMBEDDING_DIM: "abc" }));
    expect(reason).toMatch(/EMBEDDING_DIM is not a number/);
    expect(reason).toMatch(/abc/);
  });
  it("names a non-integer EMBEDDING_DIM as not a whole number", () => {
    const reason = previewUnavailableReason(readConfig({ ...FULL, EMBEDDING_DIM: "1024.5" }));
    expect(reason).toBe('EMBEDDING_DIM is not a whole number ("1024.5")');
  });
});
