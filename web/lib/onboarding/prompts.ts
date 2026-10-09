/**
 * Instructions for the onboarding agent. Versioned: a change here changes
 * what customers are shown, so it should be visible in history.
 */
import type { DraftView, Message, Research } from "./schema";

export const PROMPT_VERSION = "onboarding-2026-10-05";

export const CLASSIFY_SYSTEM = `You route messages in a sign-up chat for a public-procurement tender alert service.
Classify the user's LATEST message, using the conversation for context.
intent:
- "company": the user names the company they work for (or its website). Put the company name, plus website or country if given, in company_query.
- "pick_candidate": the user picks one of several companies the assistant listed. Put the chosen company, with its website or country, in company_query.
- "describe": the user describes products, capabilities or the tenders they want, without naming a company.
- "refine": the user corrects or adjusts drafts that already exist.
- "geography_only": the message only restricts countries or regions.
- "other": anything else.
asks_geography: true if the message asks to limit tenders by country, region or buyer location, in any language.
company_query is null unless intent is "company" or "pick_candidate".`;

export const RESEARCH_SYSTEM = `You research a company for a tender alert service. Use Google Search.
Report only what the sources say. Do not infer products or customers that no source states.
Organize by product line or business area: what it is, the technologies, and the kinds of buyers (armed forces, police, other government, civilian).
If several different companies match the name, write CANDIDATES and list each with country and website.
If you cannot identify the company, write exactly NOT_FOUND and nothing else.`;

export const DRAFT_SYSTEM = `You turn a sign-up conversation into alert profiles for a public-procurement tender service.
Tenders are matched against each profile's description by meaning, in any language. Keywords only slightly raise ranking. Excluded keywords veto tenders whose title or summary contains them.

Return JSON matching the schema. kind:
- "drafts": you can propose profiles. One profile per distinct product line or business area, at most 8.
- "candidates": the research lists several different companies and the user has not picked one. Fill candidates; drafts empty.
- "not_found": the research says NOT_FOUND. Ask what the company makes and who buys it; drafts empty.
- "clarify": you need one specific piece of information first; drafts unchanged or empty.

Each draft:
- name: short label for the product line.
- description: 2-5 sentences, a capability statement: products, technologies, typical buyers and contract types. No marketing language.
- keywords: up to 10 distinctive technical terms or standards. excluded_keywords: only themes the user rejected.
- source_ids: the numbers of the research sources this draft relies on. Cite only numbers that appear in the source list. The research text marks each supported statement with [n]; cite the numbers that appear next to the statements a draft relies on.
- from_user: true if the draft rests on what the user said rather than on research.

Rules:
- Never state a capability that neither the sources nor the user stated. Leaving something out is correct; guessing is a defect.
- Never add countries, regions or buyer locations to descriptions or keywords. The service cannot filter by location.
- On a correction, change only what the user asked to change and return all drafts, including unchanged ones.
- reply: short, in the language of the user's latest message. For drafts, say in one or two sentences what you based them on and ask the user to tick the ones they want or say what to change.`;

export const GEO_NOTICE_TEXT =
  "Filtering by country or region isn't supported yet — tenders are matched on what they're about, wherever the buyer is. I've left your profiles unchanged on that point.";

export const GEO_NOTICE_TEXT_HE =
  "עדיין אי אפשר לסנן לפי מדינה או אזור — המכרזים מותאמים לפי מה שהם עוסקים בו, בלי קשר למיקום הגוף המזמין. לא שיניתי את הפרופילים בנקודה הזו.";

export const BLOCKED_TEXT_HE =
  "אני לא יכול לחפש את זה. תאר/י במקום זה מה החברה שלך מייצרת.";

export function transcript(messages: Message[]): string {
  return messages
    .slice(-30)
    .map((m) => `${m.role === "user" ? "User" : "Assistant"}: ${m.text}`)
    .join("\n");
}

export function classifyPrompt(messages: Message[], hasDrafts: boolean, researched: string | null): string {
  return [
    `Conversation:\n${transcript(messages)}`,
    `Drafts exist: ${hasDrafts ? "yes" : "no"}`,
    `Already researched: ${researched ?? "nothing"}`,
  ].join("\n\n");
}

export function researchPrompt(query: string): string {
  return `Company: ${query}`;
}

export function draftPrompt(messages: Message[], drafts: DraftView[], research: Research | null): string {
  const sourceList = research?.sources.length
    ? research.sources.map((s) => `[${s.n}] ${s.title} — ${s.url}`).join("\n")
    : "(none)";
  const current = drafts.length
    ? JSON.stringify(
        drafts.map((d) => ({
          name: d.name,
          description: d.description,
          keywords: d.keywords,
          excluded_keywords: d.excludedKeywords,
          source_ids: d.sources.map((s) => s.n),
          from_user: d.sources.length === 0 && !d.unconfirmed,
        })),
        null,
        1,
      )
    : "(none)";
  return [
    `Conversation:\n${transcript(messages)}`,
    `Current drafts:\n${current}`,
    `Research${research ? ` on "${research.query}"` : ""}:\n${research?.text ?? "(none)"}`,
    `Sources:\n${sourceList}`,
  ].join("\n\n");
}
