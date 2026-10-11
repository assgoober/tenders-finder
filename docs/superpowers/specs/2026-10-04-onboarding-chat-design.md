# Onboarding chat — design

**Date:** 2026-10-04 · **Status:** approved in conversation, awaiting review of this file
**Scope:** `web/`, plus one read-only SQL function for the match preview
(migration 0023). No change to matching, the sweep, or alerts.

## 1. Purpose

After login, a customer should be able to say — in their own words — which
company they work for, or which tenders they want, and end up with alert
profiles that match on it. Today the only way is the `/profiles` form, which
asks for a capability paragraph most people will not write well cold.

**Success:** a new user goes from first login to one or more saved, sourced,
user-approved alert profiles in a few minutes, without writing the paragraph
themselves — and nothing the agent invented reaches a profile unnoticed.

**Non-goals:** chat history storage; changing how profiles match; a per-user
daily cost cap; any provider other than Gemini (amended 2026-10-11, approved by the user: OpenAI web search for company research only, because the Gemini free tier refuses Google Search; drafting and embeddings stay on Gemini — see ADR-30); **geography filters** (see
§2.5 — the matcher has no country filter today; that is its own feature).

## 2. User flow

1. **Entry.** A signed-in user whose organization has **no profiles** is
   redirected from `/` to `/onboarding`. `/profiles` links to it ("Set up with
   chat"). It can be skipped; the form is always one click away.
2. **First message.** Free text: a company name ("I work at Elbit Systems"),
   a direct description ("tactical radios and night vision for armies"), or
   both. The agent classifies it; there is no mode picker.
3. **Company path.** The agent researches the company on the web and answers
   in one of three ways:
   - **Found** — "Here's what I found about X": the company's site, its
     product lines (each with source links), and **one draft profile per
     product line**, each with a checkbox. A line with no supporting source is
     shown as *"unconfirmed — check this"*.
   - **Ambiguous** — several candidates (name, country, website); "which one
     is yours?"
   - **Not found** — said plainly, then "what does your company make, and who
     buys it?" Never a guess.
4. **Direct path.** A description skips research and drafts immediately.
5. **Refine.** The user corrects in plain words ("drop simulators", "add
   maintenance contracts"); drafts update in place. Draft text is also
   directly editable. **Geography is not a filter.** Matching uses only the
   description's meaning and keywords — the `filters` column exists and
   nothing reads it. When the user asks for countries or regions, the chat
   says plainly that it cannot filter by location yet, and does **not** fold
   the request into the description (where it would look applied and do
   almost nothing).
6. **Preview.** Each draft card shows what it would catch right now: "would
   match 23 open tenders", with the top three titles and deadlines. The user
   refines against real examples instead of judging a paragraph. Recomputed
   when a draft's description changes. On an empty corpus it says so ("no
   open tenders in the database yet"), not "0 matches".
7. **Approve.** "Create N profiles" saves the checked drafts, then shows the
   dashboard with "matching starts with tonight's run".
8. **Nothing persists before approval.** The conversation lives in browser
   memory only — competing contractors should not have what they typed stored,
   and an abandoned chat leaves no trace.

The chat replies in the user's language. Profile text may be in any language;
matching is cross-lingual.

## 3. Components

All in `web/`. New dependencies: `@google/genai`, `zod`; dev: `vitest`.

| Unit | Responsibility |
|---|---|
| `app/onboarding/page.tsx` | Server component. Requires sign-in and an organization; renders the chat. |
| `components/onboarding-chat.tsx` | Client. Messages, draft cards (checkbox, sources, unconfirmed flag, inline edit), "Create profiles". Holds conversation + drafts in state only. |
| `app/api/onboarding/route.ts` | `POST`. Verifies session and organization membership, enforces the message cap, calls the agent, returns `{ reply, kind, drafts, candidates, research }`. Stateless. |
| `lib/onboarding/agent.ts` | Server-only. Orchestrates the Gemini calls; takes an injectable client so tests can fake it. |
| `lib/onboarding/schema.ts` | Zod schemas for every model output. Draft limits equal the form's: name 1–120 chars, description 40–4000, ≤30 keywords and ≤30 excluded, each ≤60 chars, no `%` or `_`. |
| `lib/onboarding/prompts.ts` | Instructions, with a `PROMPT_VERSION`. |
| `lib/onboarding/embed.ts` | Server-only. Embeds a draft description exactly as the sweep embeds a profile (§4.1). |
| `supabase/migrations/0023_preview_profile_matches.sql` | `preview_profile_matches(vector, excluded text[], limit)`: count + top N open tenders a draft would match. Read-only, `SECURITY INVOKER`, granted to `authenticated` (tenders are already readable to them). |
| Saving | Existing `saveProfile` server action, once per checked draft. It re-validates server-side, so the chat can save nothing the form could not. |
| `app/page.tsx` | One added check: organization has zero profiles → `redirect("/onboarding")`. |
| `app/profiles/page.tsx` | One added link: "Set up with chat". |

### Settings

`GEMINI_API_KEY` (server-only, never `NEXT_PUBLIC_`), `GEMINI_CHAT_MODEL` (the
model id is a setting, not a constant in code — AGENTS.md). For the preview,
the **same names the scraper reads**: `EMBEDDING_PROVIDER`,
`GEMINI_EMBEDDING_MODEL`, `EMBEDDING_DIM`.

## 4. Data flow of one turn

**Company named — two Gemini calls, deliberately:**

1. **Research call**, Google Search grounding enabled. Returns prose plus
   Google's grounding metadata: the list of pages actually used. Those URLs
   come from search metadata, not from model text, so they cannot be invented.
   Sources are numbered `[1]…[n]` in the order received.
2. **Structuring call**, no tools, JSON output against the schema. Input: the
   research prose and the numbered source list. The model may cite sources
   **only by number**; code maps numbers back to URLs. A product line citing
   no number, or a number outside `1…n`, is set `unconfirmed: true` by code,
   not by the model.

**Description or correction** — only the structuring call; no search.

The research prose and source list are returned to the browser and sent back
on later turns, so a company is researched once per conversation.

### 4.1 Match preview

Per draft, after each turn that changed its description:

1. **Embed** the description with the sweep's exact recipe
   (`app/nlp/embeddings.py`): model `GEMINI_EMBEDDING_MODEL`
   (`gemini-embedding-001`), `output_dimensionality = EMBEDDING_DIM` (1024),
   `task_type = RETRIEVAL_QUERY`, then L2-normalize.
2. **Call** `preview_profile_matches` with the vector and the draft's excluded
   keywords. It mirrors `generate_matches_for_profile` (0015/0013): open or
   closing-soon tenders, deadline in the future, excluded keywords vetoed on
   title/summary, cut = `greatest(min_score default, percentile cut)` computed
   the way `refresh_profile_score_cuts` computes it. Returns the total and the
   top three by similarity.

**The risk this carries, named:** the vector is now produced in two places
(sweep in Python, preview in TypeScript). If they drift — another model,
another task type, no normalization — the preview shows plausible numbers
that are wrong, with no error. Guards:
- Both read the same environment variable names; neither hardcodes a model.
- The preview is **disabled**, with a visible note, unless
  `EMBEDDING_PROVIDER=gemini` and the vector length equals `EMBEDDING_DIM`.
- A live check (§8) embeds one text in both Python and TypeScript and
  requires cosine similarity ≥ 0.999, and requires the preview's count for a
  saved profile to equal what `generate_matches_for_profile` inserts for it.
- The SQL function's header names `generate_matches_for_profile` and
  `refresh_profile_score_cuts` as the logic it mirrors, so a change to either
  points at it.

### Model output shape (validated)

```
Turn {
  kind: "drafts" | "candidates" | "not_found" | "clarify"
  reply: string                      // what the chat says, user's language
  company?: { name, website?, country? }
  candidates?: [{ name, country?, website? }]      // kind = candidates
  drafts?: [{
    name, description, keywords[], excluded_keywords[],
    source_ids: int[]               // mapped to URLs by code
  }]
}
```

## 5. Trust and safety

- **The agent cannot write.** Its only tool is search. The single path into
  the database is the user pressing "Create profiles", through `saveProfile`.
- **Web content is untrusted input.** Injected text in a company page can at
  worst reach a draft — which the user reads, which must pass the schema, and
  which affects only that organization's own profiles (RLS + 0022 column
  grants). It cannot reach other tenants or call anything.
- **Unknown is a correct answer.** Instructions: if sources do not show it,
  omit it or leave it uncited (→ unconfirmed). Never fill gaps.
- **Invalid output never becomes a draft.** On schema failure: retry once
  with the validation error fed back; on a second failure, a plain message
  ("I couldn't put that together — try rephrasing"), no draft.

## 6. Failure handling

| Condition | User sees |
|---|---|
| `GEMINI_API_KEY` missing | "Chat setup isn't available here — set up your profile with the form", linked. |
| Quota / rate limit (429) | Same, plus "try again later". |
| Safety block (`finishReason` SAFETY / blocked prompt) | "I can't look that up — describe what your company makes instead." Conversation continues on the direct path. |
| Research finds nothing usable | The not-found branch (§2.3). |
| Network error / timeout (~45 s) | "That took too long — try again." Conversation kept. |

## 7. Cost control

- **15 messages per conversation**, enforced in the route (the browser cannot
  be trusted to count).
- One search per company named; corrections never search.
- Research cached in the conversation (§4).
- **Preview embeddings share the sweep's budget.** Same key, same daily limit
  of embedding requests (1,000 on the free tier) that the harvest already
  exhausts every night. One request per changed draft per turn: a 15-message
  conversation with 3 drafts costs at most ~45, and only when descriptions
  change. When the budget is spent, the preview shows "preview unavailable
  until tomorrow" and the chat carries on — the preview is a convenience,
  never a blocker. Previews also run during the day, after the night's sweep
  has spent the budget, so on a busy free-tier key they will often be
  unavailable; a paid embedding tier removes that.
- No per-user daily cap now — there are no paying users; easy to add.

## 8. Testing

**Unit (Vitest, no network, no key):**
- Schema rejects every form-limit violation, including `%`/`_` in keywords.
- Source mapping: cited numbers → correct URLs; no citation or out-of-range
  number → `unconfirmed`; no URL from anywhere but grounding metadata.
- Agent with a fake Gemini client: found; ambiguous; not found; direct
  description (asserts **no** search call); correction (asserts no new
  search); invalid JSON twice (friendly failure, no drafts); safety block;
  missing key.
- Route: rejects signed-out, rejects no-organization, rejects >15 messages.
- Embedding recipe: request carries `RETRIEVAL_QUERY` and `EMBEDDING_DIM`;
  output is normalized; preview disabled when provider ≠ gemini or the
  length is wrong.
- Geography: a turn asking for countries returns a reply saying it is not
  supported, and leaves the descriptions unchanged.

**Live, before calling it done (needs the Gemini key):**
1. A known company ("Elbit Systems"): multiple lines, each with real links.
2. An invented company name: "not found" and a question — no invention.
3. A Hebrew direct description: sensible draft, no search call.
4. **Embedding parity:** the same text embedded by `app/nlp/embeddings.py`
   and by `lib/onboarding/embed.ts` → cosine ≥ 0.999.
5. **Preview parity:** for a saved, embedded profile, the preview's total
   equals the rows `generate_matches_for_profile` inserts (needs tenders in
   the database — run a small sweep on the copy first).
6. Signed-in browser run against the copy's Supabase: approve two drafts →
   both rows exist, `/profiles` shows "waiting for tonight's run"; test user
   cleaned up.

Plus `npm run typecheck`, `npm run lint`, `npm run build`.

## 9. Prerequisites and open items

- **Gemini API key** for the copy (from the user) — required for live checks.
- **Tenders in the copy's database** for the preview checks — a small sweep
  (needs the same key).
- Verify at implementation time that the chosen Gemini model supports Google
  Search grounding; the two-call split (§4) means structured output and search
  never need to work in the same request.
