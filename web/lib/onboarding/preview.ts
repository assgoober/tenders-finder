/**
 * "Would match 23 open tenders right now."
 *
 * The draft is embedded with the sweep's own recipe and scored by
 * `preview_profile_matches` (migration 0023), which mirrors the matcher. A
 * preview is a convenience: every failure degrades to a status the card can
 * show, never to an error that stops the conversation.
 */
import { previewUnavailableReason, type OnboardingConfig } from "./config";
import { toAgentError } from "./errors";
import type { GeminiLike } from "./llm";
import type { DraftView, Preview } from "./schema";

export type RpcFn = (
  fn: string,
  args: Record<string, unknown>,
) => Promise<{ data: unknown; error: { message: string } | null }>;

type SqlPreview = {
  open_tenders: number;
  total: number;
  cut: number;
  top: { id: string; title: string; deadline: string | null; similarity: number }[] | null;
};

export async function previewDraft(
  description: string,
  excluded: string[],
  deps: { llm: GeminiLike; rpc: RpcFn; config: OnboardingConfig },
): Promise<Preview> {
  const off = previewUnavailableReason(deps.config);
  if (off) return { status: "disabled", reason: off };

  let vector: number[];
  try {
    vector = await deps.llm.embed(description);
  } catch (e) {
    const err = toAgentError(e);
    return err.code === "unavailable"
      ? { status: "disabled", reason: "the embedding service rejected the key" }
      : { status: "unavailable" };
  }
  if (vector.length !== deps.config.embeddingDim) {
    return {
      status: "disabled",
      reason: `the embedding has ${vector.length} dimensions, expected ${deps.config.embeddingDim}`,
    };
  }

  const { data, error } = await deps.rpc("preview_profile_matches", {
    p_embedding: `[${vector.join(",")}]`,
    p_excluded_keywords: excluded,
    p_limit: 3,
  });
  if (error || !data) return { status: "unavailable" };

  const result = data as SqlPreview;
  if (result.open_tenders === 0) return { status: "empty_corpus" };
  return {
    status: "ok",
    openTenders: result.open_tenders,
    total: result.total,
    top: (result.top ?? []).map((t) => ({
      id: t.id,
      title: t.title,
      deadline: t.deadline,
      similarity: t.similarity,
    })),
  };
}

const sameInput = (a: DraftView, b: DraftView) =>
  a.description === b.description && a.excludedKeywords.join("\n") === b.excludedKeywords.join("\n");

export async function attachPreviews(
  drafts: DraftView[],
  previous: DraftView[],
  compute: (d: DraftView) => Promise<Preview>,
): Promise<DraftView[]> {
  return Promise.all(
    drafts.map(async (d) => {
      const reuse = previous.find(
        (p) => sameInput(p, d) && p.preview !== null && p.preview.status !== "unavailable",
      );
      return { ...d, preview: reuse ? reuse.preview : await compute(d) };
    }),
  );
}
