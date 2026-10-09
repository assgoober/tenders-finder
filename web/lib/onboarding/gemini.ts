/**
 * `@google/genai` behind the `GeminiLike` interface. Server-only: it reads
 * GEMINI_API_KEY and must never be imported by a "use client" file.
 */
import { GoogleGenAI } from "@google/genai";

import type { OnboardingConfig } from "./config";
import { AgentError, toAgentError } from "./errors";
import { normalize, type GeminiLike, type GenerateRequest, type GenerateResult } from "./llm";

type ResponseLike = {
  text?: string;
  promptFeedback?: { blockReason?: string };
  candidates?: {
    finishReason?: string;
    groundingMetadata?: { groundingChunks?: { web?: { uri?: string; title?: string } }[] };
  }[];
};

/** The subset of the SDK client used here, so tests can pass a fake. */
export type GenAIClient = {
  models: {
    generateContent(args: unknown): Promise<ResponseLike>;
    embedContent(args: unknown): Promise<{ embeddings?: { values?: number[] }[] }>;
  };
};

const BLOCKING_FINISH = new Set(["SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII"]);

export function createGemini(config: OnboardingConfig, client?: GenAIClient): GeminiLike {
  const sdk: GenAIClient =
    client ??
    (new GoogleGenAI({
      apiKey: config.apiKey ?? "",
      httpOptions: { timeout: 45_000 },
    }) as unknown as GenAIClient);

  return {
    async generate(request: GenerateRequest): Promise<GenerateResult> {
      if (request.search && request.jsonSchema) {
        throw new AgentError("upstream", "search and jsonSchema cannot be combined in one call");
      }

      let response: ResponseLike;
      try {
        response = await sdk.models.generateContent({
          model: config.chatModel,
          contents: request.prompt,
          config: {
            systemInstruction: request.system,
            ...(request.search ? { tools: [{ googleSearch: {} }] } : {}),
            ...(request.jsonSchema
              ? { responseMimeType: "application/json", responseJsonSchema: request.jsonSchema }
              : {}),
          },
        });
      } catch (e) {
        throw toAgentError(e);
      }

      const candidate = response.candidates?.[0];
      const blocked =
        Boolean(response.promptFeedback?.blockReason) ||
        BLOCKING_FINISH.has(candidate?.finishReason ?? "");

      const seen = new Set<string>();
      const sources: { url: string; title: string }[] = [];
      for (const chunk of candidate?.groundingMetadata?.groundingChunks ?? []) {
        const uri = chunk.web?.uri;
        if (!uri || seen.has(uri)) continue;
        seen.add(uri);
        sources.push({ url: uri, title: chunk.web?.title || uri });
      }

      return { text: response.text ?? "", sources, blocked };
    },

    async embed(text: string): Promise<number[]> {
      try {
        const response = await sdk.models.embedContent({
          model: config.embeddingModel,
          contents: [text],
          config: { taskType: "RETRIEVAL_QUERY", outputDimensionality: config.embeddingDim },
        });
        const values = response.embeddings?.[0]?.values;
        if (!values || values.length === 0) {
          throw new AgentError("upstream", "the embedding response contained no vector");
        }
        return normalize(values);
      } catch (e) {
        throw toAgentError(e);
      }
    },
  };
}
