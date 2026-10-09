/**
 * The two things the agent needs from a model, without importing any SDK —
 * so the agent and its tests never touch the network.
 */
export type GenerateRequest = {
  system: string;
  prompt: string;
  /** Turn on Google Search grounding (research calls only). */
  search?: boolean;
  /** Ask for JSON matching this schema (structuring calls only). */
  jsonSchema?: Record<string, unknown>;
};

export type GenerateResult = {
  text: string;
  /** From grounding metadata — never parsed out of `text`. */
  sources: { url: string; title: string }[];
  blocked: boolean;
};

export interface GeminiLike {
  generate(request: GenerateRequest): Promise<GenerateResult>;
  embed(text: string): Promise<number[]>;
}

/** L2-normalize, as `scraper-service/app/nlp/embeddings.py::_normalize` does:
 *  a truncated Gemini vector is not unit length, and cosine thresholds assume it. */
export function normalize(v: number[]): number[] {
  const length = Math.sqrt(v.reduce((sum, x) => sum + x * x, 0));
  return length === 0 ? v : v.map((x) => x / length);
}
