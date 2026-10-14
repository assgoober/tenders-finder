/**
 * When is a draft's match preview out of date, and when is an answer too late?
 *
 * Pure, so the rules are testable without a browser. Each card remembers the
 * description and exclusions its preview was computed for (`previewInput`);
 * a blur only costs an embedding if the text really changed, and an answer
 * for text the user has since edited is dropped.
 */
export const DESCRIPTION_MIN = 40;
export const DESCRIPTION_MAX = 4000;

export type PreviewInput = { description: string; excludedKeywords: string[] };
export type PreviewCard = {
  description: string;
  excludedKeywords: string[];
  previewInput: PreviewInput | null;
};

const sameExcluded = (a: string[], b: string[]) => a.length === b.length && a.every((x, i) => x === b[i]);

/** The message to show under a hand-edited description, or null if it is fine. */
export function descriptionProblem(description: string): string | null {
  const n = description.trim().length;
  if (n < DESCRIPTION_MIN) return "At least 40 characters";
  if (n > DESCRIPTION_MAX) return "At most 4,000 characters";
  return null;
}

export function needsPreviewRefresh(card: PreviewCard): boolean {
  if (descriptionProblem(card.description) !== null) return false;
  const seen = card.previewInput;
  return !seen || seen.description !== card.description || !sameExcluded(seen.excludedKeywords, card.excludedKeywords);
}

export function isStaleResponse(
  card: Pick<PreviewCard, "description" | "excludedKeywords"> | undefined,
  sentDescription: string,
  sentExcluded: string[],
): boolean {
  return !card || card.description !== sentDescription || !sameExcluded(card.excludedKeywords, sentExcluded);
}
