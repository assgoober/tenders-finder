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

/** OpenAI tags cited links with utm_source=openai; drop it so the same page
 *  dedupes and the link shown to a customer is the page's own. */
function cleanUrl(raw: string): string {
  try {
    const u = new URL(raw);
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

export function parseResponsesOutput(json: unknown): GenerateResult {
  const items = ((json as { output?: OutputItem[] })?.output ?? []).filter((o) => o.type === "message");
  const parts = items.flatMap((o) => o.content ?? []);

  if (parts.some((p) => p.type === "refusal")) return { text: "", sources: [], blocked: true };

  const sources: { url: string; title: string }[] = [];
  const numberOf = new Map<string, number>();
  const texts: string[] = [];

  for (const part of parts) {
    if (part.type !== "output_text" || typeof part.text !== "string") continue;
    let text = part.text;
    // Number citations in reading order, then rewrite spans from the end so
    // earlier character offsets stay valid.
    const cites = (part.annotations ?? [])
      .filter((a) => a.type === "url_citation" && typeof a.url === "string" && /^https?:\/\//i.test(a.url))
      .filter((a) => Number.isInteger(a.start_index) && Number.isInteger(a.end_index))
      .sort((a, b) => a.start_index! - b.start_index!)
      .map((a) => {
        const url = cleanUrl(a.url!);
        const key = sameKey(url);
        if (!numberOf.has(key)) {
          sources.push({ url, title: (a.title || url).slice(0, 300) });
          numberOf.set(key, sources.length);
        }
        return { start: a.start_index!, end: a.end_index!, n: numberOf.get(key)! };
      });
    for (const c of [...cites].sort((a, b) => b.start - a.start)) {
      const start = Math.max(0, Math.min(c.start, text.length));
      const end = Math.max(start, Math.min(c.end, text.length));
      text = `${text.slice(0, start)}[${c.n}]${text.slice(end)}`;
    }
    texts.push(text);
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
        const body = await res.json().catch(() => ({}));
        const message = String((body as { error?: { message?: string } })?.error?.message ?? `HTTP ${res.status}`);
        if (res.status === 429) throw new AgentError("quota", message);
        if (res.status === 401 || res.status === 403) throw new AgentError("unavailable", message);
        throw new AgentError("upstream", message);
      }
      return parseResponsesOutput(await res.json());
    },
  };
}
