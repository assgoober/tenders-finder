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
    groundingMetadata?: {
      groundingChunks?: { web?: { uri?: string; title?: string } }[];
      groundingSupports?: { segment?: { endIndex?: number }; groundingChunkIndices?: number[] }[];
    };
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

export function annotateCitations(
  text: string,
  supports: { segment?: { endIndex?: number }; groundingChunkIndices?: number[] }[],
  chunkNumber: Map<number, number>,
): string {
  if (supports.length === 0) return text;

  // Collect labels by endIndex
  const labelsByEndIndex = new Map<number, Set<number>>();
  for (const support of supports) {
    const endIndex = support.segment?.endIndex;
    if (typeof endIndex !== "number") continue;
    const numbers = new Set<number>();
    for (const chunkIdx of support.groundingChunkIndices ?? []) {
      const n = chunkNumber.get(chunkIdx);
      if (n !== undefined) numbers.add(n);
    }
    if (numbers.size === 0) continue;
    if (!labelsByEndIndex.has(endIndex)) labelsByEndIndex.set(endIndex, new Set());
    const endIndexSet = labelsByEndIndex.get(endIndex)!;
    for (const n of Array.from(numbers)) endIndexSet.add(n);
  }

  if (labelsByEndIndex.size === 0) return text;

  // Convert text to bytes for proper offset handling
  const bytes = Buffer.from(text, "utf8");
  const offsets = Array.from(labelsByEndIndex.keys()).sort((a, b) => b - a); // Descending order
  let result = bytes;

  for (const offset of offsets) {
    const clamped = Math.max(0, Math.min(offset, result.length));
    const numbers = Array.from(labelsByEndIndex.get(offset)!).sort((a, b) => a - b);
    const label = numbers.map((n) => `[${n}]`).join("");
    const labelBytes = Buffer.from(label, "utf8");
    result = Buffer.concat([result.subarray(0, clamped), labelBytes, result.subarray(clamped)]);
  }

  return result.toString("utf8");
}

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
            ...(request.timeoutMs !== undefined ? { httpOptions: { timeout: request.timeoutMs } } : {}),
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

      // Build sources only for http(s) URIs, deduplicated by URI, numbered in first-seen order
      const uriToNumber = new Map<string, number>(); // URI -> 1-based source number
      const sources: { url: string; title: string }[] = [];
      const chunkNumber = new Map<number, number>(); // Original chunk index -> 1-based source number

      const chunks = candidate?.groundingMetadata?.groundingChunks ?? [];
      for (let i = 0; i < chunks.length; i++) {
        const uri = chunks[i].web?.uri;
        if (!uri || !/^https?:\/\//i.test(uri)) continue; // Skip non-http(s) URIs

        let sourceNumber: number;
        if (uriToNumber.has(uri)) {
          // Duplicate: use existing number
          sourceNumber = uriToNumber.get(uri)!;
        } else {
          // New: create new number and add to sources
          sourceNumber = sources.length + 1;
          uriToNumber.set(uri, sourceNumber);
          sources.push({ url: uri, title: chunks[i].web?.title || uri });
        }
        chunkNumber.set(i, sourceNumber);
      }

      // Annotate text with citation numbers based on grounding supports
      let annotatedText = response.text ?? "";
      if (request.search && candidate?.groundingMetadata?.groundingSupports) {
        annotatedText = annotateCitations(annotatedText, candidate.groundingMetadata.groundingSupports, chunkNumber);
      }

      return { text: annotatedText, sources, blocked };
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
