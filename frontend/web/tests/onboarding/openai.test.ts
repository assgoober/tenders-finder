import { describe, expect, it, vi } from "vitest";

import { createOpenAIResearcher, parseResponsesOutput } from "@/lib/onboarding/openai";

/** The shape the Responses API returned in the live probe (2026-10-11). */
function response(text: string, annotations: Array<{ start_index: number; end_index: number; url: string; title?: string }>) {
  return {
    output: [
      { type: "web_search_call", status: "completed" },
      {
        type: "message",
        content: [{ type: "output_text", text, annotations: annotations.map((a) => ({ type: "url_citation", ...a })) }],
      },
    ],
  };
}

function cite(text: string, span: string) {
  const start = text.indexOf(span);
  return { start_index: start, end_index: start + span.length };
}

/** OpenAI writes each citation into the text as an inline markdown link. */
const link = (host: string, url: string) => `([${host}](${url}))`;

describe("parseResponsesOutput", () => {
  it("replaces each cited span with a [n] marker and lists the source once", () => {
    const linkA = link("elbitsystems.com", "https://www.elbitsystems.com/segments/?utm_source=openai");
    const linkB = link("reuters.com", "https://www.reuters.com/x?utm_source=openai");
    const text = `Elbit makes radios. ${linkA} It also makes drones. ${linkB} And avionics. ${linkA}`;
    const a2 = text.lastIndexOf(linkA);
    const out = parseResponsesOutput(
      response(text, [
        { ...cite(text, linkA), url: "https://www.elbitsystems.com/segments/?utm_source=openai", title: "Segments | Elbit Systems" },
        { ...cite(text, linkB), url: "https://www.reuters.com/x?utm_source=openai", title: "Reuters" },
        { start_index: a2, end_index: a2 + linkA.length, url: "https://www.elbitsystems.com/segments/?utm_source=openai", title: "Segments | Elbit Systems" },
      ]),
    );
    expect(out.text).toBe("Elbit makes radios. [1] It also makes drones. [2] And avionics. [1]");
    expect(out.sources).toEqual([
      { url: "https://www.elbitsystems.com/segments/", title: "Segments | Elbit Systems" },
      { url: "https://www.reuters.com/x", title: "Reuters" },
    ]);
    expect(out.blocked).toBe(false);
  });

  it("treats a trailing slash as the same page (seen live: /segments and /segments/)", () => {
    const la = link("elbitsystems.com", "https://www.elbitsystems.com/segments?utm_source=openai");
    const lb = link("elbitsystems.com", "https://www.elbitsystems.com/segments/?utm_source=openai");
    const text = `Aerospace. ${la} Land. ${lb}`;
    const b = text.lastIndexOf(lb);
    const out = parseResponsesOutput(
      response(text, [
        { ...cite(text, la), url: "https://www.elbitsystems.com/segments?utm_source=openai", title: "Segments" },
        { start_index: b, end_index: b + lb.length, url: "https://www.elbitsystems.com/segments/?utm_source=openai", title: "Segments" },
      ]),
    );
    expect(out.sources).toHaveLength(1);
    expect(out.text).toBe("Aerospace. [1] Land. [1]");
  });

  it("works on Hebrew text (character offsets)", () => {
    const l = link("idf.il", "https://www.idf.il/a");
    const text = `מכ"מים לצבא. ${l}`;
    const out = parseResponsesOutput(response(text, [{ ...cite(text, l), url: "https://www.idf.il/a", title: "IDF" }]));
    expect(out.text).toBe('מכ"מים לצבא. [1]');
    expect(out.sources).toEqual([{ url: "https://www.idf.il/a", title: "IDF" }]);
  });

  it("two sources on the same span keep the text intact and show both markers", () => {
    // Review finding: the old rewrite turned this into "Radios. [2]e made here too."
    const l = link("a.example", "https://a.example/x");
    const text = `Radios. ${l} Drones are made here too.`;
    const span = cite(text, l);
    const out = parseResponsesOutput(
      response(text, [
        { ...span, url: "https://a.example/x", title: "A" },
        { ...span, url: "https://a.example/y", title: "A2" },
      ]),
    );
    expect(out.text).toBe("Radios. [1][2] Drones are made here too.");
    expect(out.sources.map((s) => s.url)).toEqual(["https://a.example/x", "https://a.example/y"]);
  });

  it("a span nested inside another becomes one marker, without eating text", () => {
    const inner = link("b.example", "https://b.example/");
    const outer = `(see ${inner} and a.example)`;
    const text = `Radios. ${outer} Drones.`;
    const out = parseResponsesOutput(
      response(text, [
        { ...cite(text, outer), url: "https://a.example/", title: "A" },
        { ...cite(text, inner), url: "https://b.example/", title: "B" },
      ]),
    );
    expect(out.text).toBe("Radios. [1][2] Drones.");
  });

  it("gives the same result whatever order the annotations arrive in", () => {
    const la = link("a.example", "https://a.example/");
    const lb = link("b.example", "https://b.example/");
    const text = `One. ${la} Two. ${lb}`;
    const anns = [
      { ...cite(text, la), url: "https://a.example/", title: "A" },
      { ...cite(text, lb), url: "https://b.example/", title: "B" },
    ];
    expect(parseResponsesOutput(response(text, [...anns].reverse()))).toEqual(parseResponsesOutput(response(text, anns)));
  });

  it("does not cut text when an offset does not point at its link (unit or index drift)", () => {
    const l = link("a.example", "https://a.example/");
    const text = `😀 Radios. ${l}`;
    const at = text.indexOf(l);
    const out = parseResponsesOutput(response(text, [{ start_index: at - 6, end_index: at - 1, url: "https://a.example/", title: "A" }]));
    expect(out.text).toBe(`😀 Radios. ${l} [1]`);
    expect(out.sources).toHaveLength(1);
  });

  it("removes a non-http citation's inline link and does not list it", () => {
    const bad = "([x](javascript:alert(1)))";
    const good = link("b.example", "https://b.example/");
    const text = `A. ${bad} B. ${good}`;
    const out = parseResponsesOutput(
      response(text, [
        { ...cite(text, bad), url: "javascript:alert(1)" },
        { ...cite(text, good), url: "https://b.example/" },
      ]),
    );
    expect(out.sources).toEqual([{ url: "https://b.example/", title: "https://b.example/" }]);
    expect(out.text).toBe("A. B. [1]");
  });

  it("numbers sources consistently across several text parts", () => {
    const la = link("a.example", "https://a.example/");
    const lb = link("b.example", "https://b.example/");
    const p1 = `First. ${la}`;
    const p2 = `Second. ${lb} Again. ${la}`;
    const again = p2.lastIndexOf(la);
    const out = parseResponsesOutput({
      output: [
        { type: "message", content: [{ type: "output_text", text: p1, annotations: [{ type: "url_citation", ...cite(p1, la), url: "https://a.example/", title: "A" }] }] },
        {
          type: "message",
          content: [
            {
              type: "output_text",
              text: p2,
              annotations: [
                { type: "url_citation", ...cite(p2, lb), url: "https://b.example/", title: "B" },
                { type: "url_citation", start_index: again, end_index: again + la.length, url: "https://a.example/", title: "A" },
              ],
            },
          ],
        },
      ],
    });
    expect(out.text).toBe("First. [1]\nSecond. [2] Again. [1]");
  });

  it("leaves a link's own query string alone when there is no utm_source", () => {
    const url = "https://a.example/s?q=a%20b&flag";
    const l = link("a.example", url);
    const text = `X. ${l}`;
    const out = parseResponsesOutput(response(text, [{ ...cite(text, l), url, title: "A" }]));
    expect(out.sources[0].url).toBe(url);
  });

  it("returns the text unchanged with no sources when nothing was cited", () => {
    expect(parseResponsesOutput(response("NOT_FOUND", []))).toEqual({ text: "NOT_FOUND", sources: [], blocked: false });
  });

  it("reports a refusal as blocked, not as an error", () => {
    const refused = { output: [{ type: "message", content: [{ type: "refusal", refusal: "I can't help with that." }] }] };
    expect(parseResponsesOutput(refused)).toEqual({ text: "", sources: [], blocked: true });
  });

  it("reports a content-filtered (incomplete) response as blocked", () => {
    const filtered = { status: "incomplete", incomplete_details: { reason: "content_filter" }, output: [] };
    expect(parseResponsesOutput(filtered)).toEqual({ text: "", sources: [], blocked: true });
  });
});

describe("createOpenAIResearcher", () => {
  const ok = (body: unknown) => ({ ok: true, status: 200, json: async () => body }) as Response;

  it("calls the Responses API with web search, the configured model and the instructions", async () => {
    const fetchFn = vi.fn(async (_url: string, _init?: RequestInit) => ok(response("Radios.", [])));
    const r = createOpenAIResearcher({ apiKey: "sk-test", model: "research-model" }, fetchFn as unknown as typeof fetch);
    const out = await r.research({ system: "S", prompt: "Company: Elbit", timeoutMs: 12_000 });
    const [url, init] = fetchFn.mock.calls[0];
    expect(url).toBe("https://api.openai.com/v1/responses");
    expect((init!.headers as Record<string, string>).Authorization).toBe("Bearer sk-test");
    expect(JSON.parse(String(init!.body))).toEqual({
      model: "research-model",
      instructions: "S",
      input: "Company: Elbit",
      tools: [{ type: "web_search" }],
    });
    expect(init!.signal).toBeInstanceOf(AbortSignal);
    expect(out.text).toBe("Radios.");
  });

  it.each([
    [429, "quota"],
    [401, "unavailable"],
    [403, "unavailable"],
    [500, "upstream"],
  ])("maps HTTP %s to %s", async (status, code) => {
    const fetchFn = vi.fn(async () => ({ ok: false, status, json: async () => ({ error: { message: "nope" } }) }) as Response);
    const r = createOpenAIResearcher({ apiKey: "k", model: "m" }, fetchFn as unknown as typeof fetch);
    await expect(r.research({ system: "S", prompt: "P" })).rejects.toMatchObject({ code });
  });

  it("maps a 400 about the model to unavailable, so a wrong OPENAI_RESEARCH_MODEL is visible", async () => {
    const fetchFn = vi.fn(
      async () =>
        ({ ok: false, status: 400, json: async () => ({ error: { message: "The model `nope` does not exist", code: "model_not_found" } }) }) as Response,
    );
    const r = createOpenAIResearcher({ apiKey: "k", model: "nope" }, fetchFn as unknown as typeof fetch);
    await expect(r.research({ system: "S", prompt: "P" })).rejects.toMatchObject({ code: "unavailable" });
  });

  it("maps an aborted request to timeout", async () => {
    const fetchFn = vi.fn(async () => {
      throw Object.assign(new Error("The operation was aborted."), { name: "TimeoutError" });
    });
    const r = createOpenAIResearcher({ apiKey: "k", model: "m" }, fetchFn as unknown as typeof fetch);
    await expect(r.research({ system: "S", prompt: "P", timeoutMs: 5 })).rejects.toMatchObject({ code: "timeout" });
  });
});
