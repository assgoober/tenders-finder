# AI relevance check — design

**Date:** 2026-10-13 · **Status:** approved in conversation, awaiting review of this file
**Part 1 of 3** of "send only relevant tenders": (1) this relevance check,
(2) country filter, (3) procurement category codes (CPV). Parts 2 and 3 get
their own specs.

## 1. Why

Measured on 299 real TED tenders (2026-10-12) with three realistic profiles,
each restricted to one country:

| Profile → country | Similarity floor 0.58 alone sends | Of which clearly irrelevant (examples) |
|---|---|---|
| IT/software → Poland | 11 | building supervision, a conference, bus transport |
| Medical → Germany | 4 | textile machinery (ranked 2nd), defence equipment |
| Construction → France | 40 | mostly: road transport, water-supply operation, … |

Similarity scores cluster in 0.57–0.66, so relevant and irrelevant tenders
overlap; no single threshold separates them. The percentile cut (0013) either
sends nothing for country-restricted profiles (global percentile) or one
tender (per-country). Similarity is a good **candidate** generator and a poor
**judge**.

**Success:** alerts contain the clearly relevant tenders (e.g. the five Polish
IT tenders above) and drop most of the noise, measured on a labelled set
(§7) — not asserted.

**Non-goals:** country filtering and CPV (parts 2 and 3); changing how
similarity matching works; deciding whether a customer should bid (the model
only judges whether a tender is *about* what the profile describes —
AGENTS.md: "never decides whether to bid").

## 2. Where it sits

```
tender ingested ──trigger──▶ tender_matches row (similarity ≥ cut)   [unchanged]
                                   │
nightly sweep:  check_pending_relevance()   ◀── NEW, before alerts
                                   │  verdict relevant / not_relevant + reason
                deliver_match_alerts()      ◀── reads pending_match_alerts (changed)
```

The check runs in the Python sweep (`backend/scraper-service`), in `finish_sweep`,
**after** profiles are embedded/backfilled and **before** `deliver_match_alerts`.

## 3. The check

- Select matches with `relevance is null`, not dismissed, not alerted, tender
  open — grouped by profile, oldest first.
- Per profile, batches of up to 20 tenders per OpenAI call. Input per tender:
  title, buyer name, buyer country, CPV codes, the listing description
  (truncated to 600 chars). Input per profile: name, description, keywords,
  excluded keywords.
- Output (Pydantic-validated, AGENTS.md "LLM output is never trusted"):
  `{ "verdicts": [ { "match_id": str, "relevant": bool, "reason": str ≤ 200 } ] }`.
  Every requested `match_id` must appear exactly once; unknown ids ignored.
- Invalid output: retry once with the validation error fed back; a second
  failure leaves the batch unchecked (it is retried next run, §4).
- A refusal (`stop`/refusal output) leaves the batch unchecked; logged.
- Instructions: judge only whether the tender's subject matches the profile's
  products/services; "not sure" → `relevant: true` with a reason saying so
  (prefer a borderline alert over a missed tender); never judge price,
  eligibility or whether to bid.

**Provider:** OpenAI Responses API, structured output, plain HTTP (`httpx`),
model from `OPENAI_RELEVANCE_MODEL`; key `OPENAI_API_KEY` in the root `.env`.
No search tool. Gemini is not used for this (free-tier limits).

**Cost control:** a nightly cap `RELEVANCE_MAX_CALLS` (default 50 calls ≈
1,000 matches); what exceeds it waits for the next night. Each run logs calls,
matches checked, relevant/not counts.

## 4. When the check can't run

Decided: **hold, then send after 24 h.** A match unchecked for 24 hours since
it was created is delivered anyway, marked "not yet checked" in the email and
the dashboard. A missed tender is the worse failure.

## 5. Data (migration 0025)

`tender_matches` gains:
- `relevance text check (relevance in ('relevant','not_relevant'))` — null = unchecked
- `relevance_reason text`
- `relevance_checked_at timestamptz`

`pending_match_alerts` is redefined (same signature/return as 0018) to deliver
a match only if `relevance = 'relevant'`, or `relevance is null and
m.created_at < now() - interval '24 hours'` (returned with an
`unchecked boolean` column so the email can mark it). `not_relevant` is never
delivered. Writes to the new columns: service role only. Today the
`tender_matches_update` policy lets an org member update ANY column of their
own matches through the API (the web app itself never writes this table —
checked 2026-10-13), so a member could mark their own noise "relevant".
0025 therefore revokes UPDATE on `tender_matches` from `authenticated` and
grants it back on `dismissed_at` only (the one column a member legitimately
changes), the same column-grant pattern as 0022.

## 6. Customer-facing

- **Email:** only relevant matches (and >24 h unchecked ones, marked); each
  shows the one-line reason.
- **Dashboard feed:** hides `not_relevant` by default; shows the reason on each
  match. (No restyling — UI follows existing components; the dashboard
  redesign is on hold for the UX design.)
- **Onboarding chat preview:** instead of "would match 23", run the check on
  the top 10 similarity candidates of a draft (one OpenAI call, from the web
  app via the same `openai.ts` plain-fetch style) and show "**7 of the top 10
  checked are relevant**" with their titles. Falls back to the plain count,
  labelled "not checked", if OpenAI is unavailable.

## 7. Proving it works (acceptance)

1. **Labelled set:** the three profiles of §1 × their country's tenders; every
   candidate above the 0.58 floor labelled relevant / not by Claude, written to
   `docs/superpowers/evals/2026-10-13-relevance-labels.md` for the user to
   spot-check.
2. **Measure:** for similarity-only vs with-check — relevant tenders kept
   (recall) and irrelevant tenders sent (noise).
3. **Bar:** keep all clearly relevant tenders of the set (e.g. the five Polish
   IT tenders) and remove at least most of the noise. If not met, change the
   instructions, not the bar. Results recorded in the eval file.
4. **Unit tests (fake OpenAI):** verdict schema; retry; refusal; unknown/missing
   ids; batching per profile; nightly cap; 24 h hold and release;
   `not_relevant` never delivered; dashboard filtering; preview fallback.

## 8. Open items

- Exact `OPENAI_RELEVANCE_MODEL` chosen from the account's model list at
  implementation (default proposal: the same mid-size model used for
  research).
- Whether to re-check a match when the profile's text changes (proposal: yes —
  0021's re-embed already regenerates matches; new matches start unchecked).
