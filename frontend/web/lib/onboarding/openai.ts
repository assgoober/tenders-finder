/**
 * Company web research through OpenAI's Responses API and its built-in
 * `web_search` tool. Server-only: reads OPENAI_API_KEY.
 *
 * Why it exists: on Gemini's free tier, Google Search grounding is refused
 * outright (429 on every model, measured 2026-10-11). OpenAI is used for this
 * one job only — never for drafting, and never for embeddings, which must come
 * from the same model as the stored corpus.
 *
 * The trust contract matches the Gemini path (see llm.ts `Researcher`):
 * sources come from `url_citation` annotations, never from the model's text.
 * The Responses API writes each citation into the text as an inline markdown
 * link; that span is replaced by a [n] marker, so the drafting model sees
 * numbers, not URLs, and can only cite what the search tool actually returned.
 *
 * Plain fetch, no SDK: one endpoint, one tool, and one less dependency.
 */
import { AgentError, toAgentError } from "./errors";
import type { GenerateResult, Researcher } from "./llm";

const ENDPOINT = "https://api.openai.com/v1/responses";
const DEFAULT_TIMEOUT_MS = 45_000;

type Annotation = { type?: string; start_index?: number; end_index?: number; url?: string; title?: string };
type ContentPart = { type?: string; text?: string; annotations?: Annotation[]; refusal?: string };
type OutputItem = { type?: string; content?: ContentPart[] };
type ResponsesBody = { status?: string; incomplete_details?: { reason?: string }; output?: OutputItem[] };

const isHttp = (url: string) => /^https?:\/\//i.test(url);

/** OpenAI tags cited links with utm_source=openai; drop it so the same page
 *  dedupes and the link shown to a customer is the page's own. Only rewritten
 *  when the tag is there: URLSearchParams re-serializes the whole query, which
 *  would turn `a%20b&flag` into `a+b&flag=` on links that never had it. */
function cleanUrl(raw: string): string {
  try {
    const u = new URL(raw);
    if (!u.searchParams.has("utm_source")) return raw;
    u.searchParams.delete("utm_source");
    return u.toString();
  } catch {
    return raw;
  }
}

/** Same page, different spelling: compared without a trailing slash (seen
 *  live: /segments and /segments/ cited as two sources). Display keeps the
 *  first-seen URL. */
function sameKey(url: string): string {
  return url.replace(/\/(?=$|[?#])/, "");
}

/** The host as it appears in the inline link OpenAI writes, e.g. "elbitsystems.com". */
function hostOf(url: string): string | null {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return null;
  }
}

type Span = { start: number; end: number; numbers: number[] };

/**
 * Replace cited spans with [n] markers. Two things the first version got
 * wrong, both found in review:
 *  - Spans can repeat or nest (several sources behind one claim). Rewriting
 *    them one by one cut real text ("Radios. [2]e made here too."). Spans are
 *    now merged first; a merged span carries every number it covered.
 *  - Offsets are trusted only when the span really holds its link. JavaScript
 *    slices UTF-16 units; if the API counted code points, everything after an
 *    emoji would shift and the wrong text would be cut. A span that does not
 *    contain the cited site's host is left in place and its marker appended,
 *    so no text is ever lost to a bad offset.
 * Non-http citations become spans with no number: their inline link (with its
 * URL) is removed, and they never become sources.
 */
function rewrite(text: string, spans: Span[], appended: number[]): string {
  const merged: Span[] = [];
  for (const s of [...spans].sort((a, b) => a.start - b.start || b.end - a.end)) {
    const last = merged[merged.length - 1];
    if (last && s.start < last.end) {
      last.end = Math.max(last.end, s.end);
      last.numbers.push(...s.numbers);
    } else {
      merged.push({ ...s, numbers: [...s.numbers] });
    }
  }
  let out = text;
  for (const m of merged.reverse()) {
    const label = Array.from(new Set(m.numbers)).sort((a, b) => a - b).map((n) => `[${n}]`).join("");
    out = `${out.slice(0, m.start)}${label}${out.slice(m.end)}`;
  }
  if (appended.length) {
    out = `${out} ${Array.from(new Set(appended)).sort((a, b) => a - b).map((n) => `[${n}]`).join("")}`;
  }
  // A removed non-http link leaves a double space behind; collapse spaces only.
  return out.replace(/ {2,}/g, " ");
}

export function parseResponsesOutput(json: unknown): GenerateResult {
  const body = (json ?? {}) as ResponsesBody;
  if (body.status === "incomplete" && body.incomplete_details?.reason === "content_filter") {
    return { text: "", sources: [], blocked: true };
  }
  const parts = (body.output ?? []).filter((o) => o.type === "message").flatMap((o) => o.content ?? []);
  if (parts.some((p) => p.type === "refusal")) return { text: "", sources: [], blocked: true };

  const sources: { url: string; title: string }[] = [];
  const numberOf = new Map<string, number>();
  const number = (url: string, title: string | undefined) => {
    const key = sameKey(url);
    if (!numberOf.has(key)) {
      sources.push({ url, title: (title || url).slice(0, 300) });
      numberOf.set(key, sources.length);
    }
    return numberOf.get(key)!;
  };

  const texts: string[] = [];
  for (const part of parts) {
    if (part.type !== "output_text" || typeof part.text !== "string") continue;
    const text = part.text;
    const spans: Span[] = [];
    const appended: number[] = [];
    // Reading order decides the numbering, whatever order the API lists them in.
    const cites = (part.annotations ?? [])
      .filter((a) => a.type === "url_citation" && typeof a.url === "string")
      .filter((a) => Number.isInteger(a.start_index) && Number.isInteger(a.end_index))
      .sort((a, b) => a.start_index! - b.start_index!);
    for (const a of cites) {
      const start = Math.max(0, Math.min(a.start_index!, text.length));
      const end = Math.max(start, Math.min(a.end_index!, text.length));
      const spanText = text.slice(start, end);
      if (!isHttp(a.url!)) {
        // Only cut it if the span really is that link; otherwise leave the text be.
        if (end > start && spanText.includes(a.url!)) spans.push({ start, end, numbers: [] });
        continue;
      }
      const url = cleanUrl(a.url!);
      const n = number(url, a.title);
      const host = hostOf(url);
      if (end > start && host && spanText.includes(host)) spans.push({ start, end, numbers: [n] });
      else appended.push(n);
    }
    texts.push(rewrite(text, spans, appended));
  }

  return { text: texts.join("\n"), sources, blocked: false };
}

export function createOpenAIResearcher(
  config: { apiKey: string; model: string },
  fetchFn: typeof fetch = fetch,
): Researcher {
  return {
    async research({ system, prompt, timeoutMs }) {
      let res: Response;
      try {
        res = await fetchFn(ENDPOINT, {
          method: "POST",
          headers: { Authorization: `Bearer ${config.apiKey}`, "Content-Type": "application/json" },
          body: JSON.stringify({
            model: config.model,
            instructions: system,
            input: prompt,
            tools: [{ type: "web_search" }],
          }),
          signal: AbortSignal.timeout(timeoutMs ?? DEFAULT_TIMEOUT_MS),
        });
      } catch (e) {
        const name = (e as { name?: string })?.name;
        if (name === "TimeoutError" || name === "AbortError") throw new AgentError("timeout");
        throw toAgentError(e);
      }
      if (!res.ok) {
        // Same mapping as the Gemini adapter: 429 → quota, 401/403 → the
        // service is unavailable with this key, anything else → upstream.
        // A 400 about the model (wrong OPENAI_RESEARCH_MODEL, or a model
        // without web search) is a configuration problem, not a passing
        // glitch: report it as unavailable so it does not look retryable.
        const err = ((await res.json().catch(() => ({}))) as { error?: { message?: string; code?: string } }).error;
        const message = String(err?.message ?? `HTTP ${res.status}`);
        if (res.status === 429) throw new AgentError("quota", message);
        if (res.status === 401 || res.status === 403) throw new AgentError("unavailable", message);
        if (res.status === 400 && (/model/i.test(err?.code ?? "") || /model/i.test(message))) {
          throw new AgentError("unavailable", message);
        }
        throw new AgentError("upstream", message);
      }
      return parseResponsesOutput(await res.json());
    },
  };
}
