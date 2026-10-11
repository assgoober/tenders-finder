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

describe("parseResponsesOutput", () => {
  it("replaces each cited span with a [n] marker and lists the source once", () => {
    const linkA = "([elbitsystems.com](https://www.elbitsystems.com/segments/?utm_source=openai))";
    const linkB = "([reuters.com](https://www.reuters.com/x?utm_source=openai))";
    const linkA2 = "([elbitsystems.com](https://www.elbitsystems.com/segments/?utm_source=openai))";
    const text = `Elbit makes radios. ${linkA} It also makes drones. ${linkB} And avionics. ${linkA2}`;
    const a1 = cite(text, linkA);
    const b = cite(text, linkB);
    const a2 = { start_index: text.lastIndexOf(linkA2), end_index: text.lastIndexOf(linkA2) + linkA2.length };
    const out = parseResponsesOutput(
      response(text, [
        { ...a1, url: "https://www.elbitsystems.com/segments/?utm_source=openai", title: "Segments | Elbit Systems" },
        { ...b, url: "https://www.reuters.com/x?utm_source=openai", title: "Reuters" },
        { ...a2, url: "https://www.elbitsystems.com/segments/?utm_source=openai", title: "Segments | Elbit Systems" },
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
    const text = "Aerospace. (a) Land. (b)";
    const out = parseResponsesOutput(
      response(text, [
        { ...cite(text, "(a)"), url: "https://www.elbitsystems.com/segments?utm_source=openai", title: "Segments" },
        { ...cite(text, "(b)"), url: "https://www.elbitsystems.com/segments/?utm_source=openai", title: "Segments" },
      ]),
    );
    expect(out.sources).toHaveLength(1);
    expect(out.text).toBe("Aerospace. [1] Land. [1]");
  });

  it("works on Hebrew text (character offsets)", () => {
    const link = "([idf.il](https://www.idf.il/a))";
    const text = `מכ"מים לצבא. ${link}`;
    const out = parseResponsesOutput(response(text, [{ ...cite(text, link), url: "https://www.idf.il/a", title: "IDF" }]));
    expect(out.text).toBe('מכ"מים לצבא. [1]');
    expect(out.sources).toEqual([{ url: "https://www.idf.il/a", title: "IDF" }]);
  });

  it("drops non-http(s) citations and uses the URL when there is no title", () => {
    const text = "A. (x) B. (y)";
    const out = parseResponsesOutput(
      response(text, [
        { ...cite(text, "(x)"), url: "javascript:alert(1)" },
        { ...cite(text, "(y)"), url: "https://b.example/" },
      ]),
    );
    expect(out.sources).toEqual([{ url: "https://b.example/", title: "https://b.example/" }]);
    expect(out.text).toBe("A. (x) B. [1]");
  });

  it("returns the text unchanged with no sources when nothing was cited", () => {
    expect(parseResponsesOutput(response("NOT_FOUND", []))).toEqual({ text: "NOT_FOUND", sources: [], blocked: false });
  });

  it("reports a refusal as blocked, not as an error", () => {
    const refused = { output: [{ type: "message", content: [{ type: "refusal", refusal: "I can't help with that." }] }] };
    expect(parseResponsesOutput(refused)).toEqual({ text: "", sources: [], blocked: true });
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

  it("maps an aborted request to timeout", async () => {
    const fetchFn = vi.fn(async () => {
      throw Object.assign(new Error("The operation was aborted."), { name: "TimeoutError" });
    });
    const r = createOpenAIResearcher({ apiKey: "k", model: "m" }, fetchFn as unknown as typeof fetch);
    await expect(r.research({ system: "S", prompt: "P", timeoutMs: 5 })).rejects.toMatchObject({ code: "timeout" });
  });
});
