/**
 * Sources come from the search tool's citation metadata (Gemini grounding or OpenAI url_citation), never from model text.
 *
 * The research call returns the pages it actually used; they are numbered
 * here. The drafting call may cite only those numbers, and this module turns
 * numbers back into links. A draft that cites nothing valid is flagged
 * `unconfirmed` by code — the model cannot vouch for itself.
 */
import type { DraftView, ModelDraft, Research, Source } from "./schema";

export function numberSources(raw: { url: string; title: string }[]): Source[] {
  const seen = new Set<string>();
  const out: Source[] = [];
  for (const { url, title } of raw) {
    if (!/^https?:\/\//i.test(url) || seen.has(url)) continue;
    seen.add(url);
    out.push({ n: out.length + 1, url, title: (title || url).slice(0, 300) });
  }
  return out;
}

export function toDraftView(
  d: ModelDraft,
  research: Research | null,
  key: string,
  researchedThisTurn: boolean,
): DraftView {
  let sources: Source[] = [];
  if (research) {
    const byNumber = new Map(research.sources.map((s) => [s.n, s]));
    const cited = Array.from(new Set(d.source_ids));
    sources = cited.map((n) => byNumber.get(n)).filter((s): s is Source => s !== undefined);
  }
  return {
    key,
    name: d.name,
    description: d.description,
    keywords: d.keywords,
    excludedKeywords: d.excluded_keywords,
    sources,
    // No research means the user described it themselves: nothing to confirm.
    // On the turn that researched, the model's own `from_user` claim is
    // ignored (it cannot vouch for itself): only real citations clear the
    // flag. On later turns the research is carried over and the model is
    // only echoing what the user said, so `from_user` is honoured.
    unconfirmed:
      research !== null && sources.length === 0 && (researchedThisTurn || !d.from_user),
    preview: null,
  };
}
