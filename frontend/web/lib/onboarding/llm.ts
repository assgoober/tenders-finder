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
  /** Per-call timeout; when unset the adapter's own default applies. Set from
   *  the turn's deadline so a call that starts late cannot outlive the route. */
  timeoutMs?: number;
};

export type GenerateResult = {
  text: string;
  /** From grounding metadata — never parsed out of `text`. */
  sources: { url: string; title: string }[];
  blocked: boolean;
};

/**
 * Web research for a named company. Gemini does it with Google Search when
 * the key allows it; the free tier does not, so OpenAI's web search can take
 * this one job (and only this one) — see config.researchProvider.
 * Contract: `sources` come from the search tool's citation metadata, never
 * from the model's text, deduplicated, http(s) only, and `text` marks cited
 * statements with [n] where n is the 1-based position in `sources`.
 */
export interface Researcher {
  research(request: { system: string; prompt: string; timeoutMs?: number }): Promise<GenerateResult>;
}

export interface GeminiLike {
  generate(request: GenerateRequest): Promise<GenerateResult>;
  embed(text: string): Promise<number[]>;
}

/** L2-normalize, as `backend/scraper-service/app/nlp/embeddings.py::_normalize` does:
 *  a truncated Gemini vector is not unit length, and cosine thresholds assume it. */
export function normalize(v: number[]): number[] {
  const length = Math.sqrt(v.reduce((sum, x) => sum + x * x, 0));
  return length === 0 ? v : v.map((x) => x / length);
}
