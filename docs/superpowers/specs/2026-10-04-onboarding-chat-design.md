# Onboarding chat — design

**Date:** 2026-10-04 · **Status:** approved in conversation, awaiting review of this file
**Scope:** `web/` only. No schema change, no change to matching, the sweep, or alerts.

## 1. Purpose

After login, a customer should be able to say — in their own words — which
company they work for, or which tenders they want, and end up with alert
profiles that match on it. Today the only way is the `/profiles` form, which
asks for a capability paragraph most people will not write well cold.

**Success:** a new user goes from first login to one or more saved, sourced,
user-approved alert profiles in a few minutes, without writing the paragraph
themselves — and nothing the agent invented reaches a profile unnoticed.

**Non-goals:** chat history storage; changing how profiles match; a per-user
daily cost cap; any provider other than Gemini.

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
   maintenance contracts", "only Europe and Israel"); drafts update in place.
   Draft text is also directly editable.
6. **Approve.** "Create N profiles" saves the checked drafts, then shows the
   dashboard with "matching starts with tonight's run".
7. **Nothing persists before approval.** The conversation lives in browser
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
| Saving | Existing `saveProfile` server action, once per checked draft. It re-validates server-side, so the chat can save nothing the form could not. |
| `app/page.tsx` | One added check: organization has zero profiles → `redirect("/onboarding")`. |
| `app/profiles/page.tsx` | One added link: "Set up with chat". |

### Settings

`GEMINI_API_KEY` (server-only, never `NEXT_PUBLIC_`), `GEMINI_CHAT_MODEL` (the
model id is a setting, not a constant in code — AGENTS.md).

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

**Live, before calling it done (needs the Gemini key):**
1. A known company ("Elbit Systems"): multiple lines, each with real links.
2. An invented company name: "not found" and a question — no invention.
3. A Hebrew direct description: sensible draft, no search call.
4. Signed-in browser run against the copy's Supabase: approve two drafts →
   both rows exist, `/profiles` shows "waiting for tonight's run"; test user
   cleaned up.

Plus `npm run typecheck`, `npm run lint`, `npm run build`.

## 9. Prerequisites and open items

- **Gemini API key** for the copy (from the user) — required for live checks.
- Verify at implementation time that the chosen Gemini model supports Google
  Search grounding; the two-call split (§4) means structured output and search
  never need to work in the same request.
