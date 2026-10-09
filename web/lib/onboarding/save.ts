/**
 * Save the drafts the user approved, through the same server action as the
 * form. Each draft at most once: a retry after a partial failure skips the
 * ones that already went in.
 */
import type { ProfileInput, ProfileResult } from "@/lib/actions/profiles";

import type { DraftView } from "./schema";

export type SaveOutcome = { key: string; ok: true; id: string } | { key: string; ok: false; error: string };

export async function saveDrafts(
  drafts: DraftView[],
  alreadySaved: ReadonlySet<string>,
  save: (input: ProfileInput) => Promise<ProfileResult>,
): Promise<SaveOutcome[]> {
  const outcomes: SaveOutcome[] = [];
  for (const d of drafts) {
    if (alreadySaved.has(d.key)) continue;
    try {
      const result = await save({
        name: d.name,
        description: d.description,
        keywords: d.keywords.join(", "),
        excludedKeywords: d.excludedKeywords.join(", "),
        notifyEmail: true,
      });
      outcomes.push(result.ok ? { key: d.key, ok: true, id: result.id } : { key: d.key, ok: false, error: result.error });
    } catch (e) {
      outcomes.push({ key: d.key, ok: false, error: e instanceof Error ? e.message : "Could not save." });
    }
  }
  return outcomes;
}
