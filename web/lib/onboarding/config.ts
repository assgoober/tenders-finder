/**
 * Settings for the onboarding agent, read from the environment.
 *
 * The embedding settings use the SAME variable names the scraper reads
 * (`scraper-service/app/config.py`), because the preview's vectors must be
 * comparable with the corpus the sweep embedded. A vector from another model
 * has the right shape and a meaningless score — so the preview is disabled,
 * not guessed, whenever these do not line up.
 */
export type OnboardingConfig = {
  apiKey: string | null;
  chatModel: string | null;
  embeddingProvider: string | null;
  embeddingModel: string | null;
  embeddingDim: number;
  /** @internal Raw EMBEDDING_DIM value for error messages. */
  _embeddingDimRaw?: string;
  /** OpenAI is used for company web research only (never for embeddings —
   *  those must match the corpus — and never for drafting). Optional so
   *  hand-built configs in tests need not mention it. */
  openaiApiKey?: string | null;
  openaiResearchModel?: string | null;
};

/** The `vector(1024)` columns in migrations 0003/0004 fix this. */
const DB_VECTOR_DIM = 1024;

export function readConfig(env: Record<string, string | undefined> = process.env): OnboardingConfig {
  const v = (name: string) => env[name]?.trim() || null;
  const dimStr = v("EMBEDDING_DIM");
  const dimNum = Number(dimStr ?? DB_VECTOR_DIM);
  return {
    apiKey: v("GEMINI_API_KEY"),
    chatModel: v("GEMINI_CHAT_MODEL") ?? v("GEMINI_MODEL"),
    embeddingProvider: v("EMBEDDING_PROVIDER"),
    embeddingModel: v("GEMINI_EMBEDDING_MODEL"),
    embeddingDim: dimNum,
    ...(dimStr ? { _embeddingDimRaw: dimStr } : {}),
    openaiApiKey: v("OPENAI_API_KEY"),
    openaiResearchModel: v("OPENAI_RESEARCH_MODEL"),
  };
}

/** Which service researches a named company. OpenAI only when both of its
 *  settings are present; otherwise Gemini with Google Search, as before. */
export function researchProvider(c: OnboardingConfig): "openai" | "gemini" {
  return c.openaiApiKey && c.openaiResearchModel ? "openai" : "gemini";
}

export function chatUnavailableReason(c: OnboardingConfig): string | null {
  if (!c.apiKey) return "GEMINI_API_KEY is not set";
  if (!c.chatModel) return "GEMINI_CHAT_MODEL (or GEMINI_MODEL) is not set";
  return null;
}

export function previewUnavailableReason(c: OnboardingConfig): string | null {
  const chat = chatUnavailableReason(c);
  if (chat) return chat;
  if (c.embeddingProvider !== "gemini") {
    return "EMBEDDING_PROVIDER is not gemini, so a preview vector would not be comparable with the corpus";
  }
  if (!c.embeddingModel) return "GEMINI_EMBEDDING_MODEL is not set";
  // Check if embeddingDim is not a valid finite integer
  if (!Number.isFinite(c.embeddingDim)) {
    const raw = c._embeddingDimRaw ? ` ("${c._embeddingDimRaw}")` : "";
    return `EMBEDDING_DIM is not a number${raw}`;
  }
  if (!Number.isInteger(c.embeddingDim)) {
    const raw = c._embeddingDimRaw ? ` ("${c._embeddingDimRaw}")` : "";
    return `EMBEDDING_DIM is not a whole number${raw}`;
  }
  if (c.embeddingDim !== DB_VECTOR_DIM) return `EMBEDDING_DIM must be ${DB_VECTOR_DIM}, the database column's size`;
  return null;
}
