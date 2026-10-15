/**
 * `@google/genai` behind the `GeminiLike` interface. Server-only: it reads
 * GEMINI_API_KEY and must never be imported by a "use client" file.
 */
import { GoogleGenAI } from "@google/genai";

import type { OnboardingConfig } from "./config";
import { AgentError, isOverloaded, toAgentError } from "./errors";
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

/** Waits between attempts when Google reports it is overloaded. */
export const OVERLOAD_BACKOFF_MS = [1_000, 2_500];

const realSleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

/**
 * Run `call` and retry it while Google reports overload (503), up to
 * OVERLOAD_BACKOFF_MS.length more times. `call` gets the attempt number, so
 * a caller can switch to a fallback model for the later attempts. A retry is only made if the call's
 * own time budget still has room for the wait plus a real attempt, so a
 * retry can never push a turn past the route's limit.
 */
export async function withOverloadRetry<T>(
  call: (timeoutMs: number | undefined, attempt: number) => Promise<T>,
  timeoutMs: number | undefined,
  sleep: (ms: number) => Promise<void> = realSleep,
  now: () => number = Date.now,
): Promise<T> {
  const start = now();
  for (let attempt = 0; ; attempt++) {
    const left = timeoutMs === undefined ? undefined : timeoutMs - (now() - start);
    try {
      return await call(left, attempt);
    } catch (e) {
      const wait = OVERLOAD_BACKOFF_MS[attempt];
      const roomLeft = timeoutMs === undefined ? Infinity : timeoutMs - (now() - start) - wait;
      if (!isOverloaded(e) || wait === undefined || roomLeft < 3_000) throw e;
      await sleep(wait);
    }
  }
}

export function createGemini(
  config: OnboardingConfig,
  client?: GenAIClient,
  sleep: (ms: number) => Promise<void> = realSleep,
): GeminiLike {
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
        response = await withOverloadRetry(
          (timeout, attempt) =>
            sdk.models.generateContent({
              // The last attempt goes to the fallback model when one is set:
              // overload bursts hit one model at a time (2026-10-15:
              // 3.6-flash failed for minutes while 3.7-flash answered).
              model:
                config.chatFallbackModel && attempt >= OVERLOAD_BACKOFF_MS.length
                  ? config.chatFallbackModel
                  : config.chatModel,
              contents: request.prompt,
              config: {
                systemInstruction: request.system,
                ...(timeout !== undefined ? { httpOptions: { timeout } } : {}),
                ...(request.search ? { tools: [{ googleSearch: {} }] } : {}),
                ...(request.jsonSchema
                  ? { responseMimeType: "application/json", responseJsonSchema: request.jsonSchema }
                  : {}),
              },
            }),
          request.timeoutMs,
          sleep,
        );
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
        const response = await withOverloadRetry(
          () =>
            sdk.models.embedContent({
              model: config.embeddingModel,
              contents: [text],
              config: { taskType: "RETRIEVAL_QUERY", outputDimensionality: config.embeddingDim },
            }),
          undefined,
          sleep,
        );
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
