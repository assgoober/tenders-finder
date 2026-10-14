"use server";

/**
 * Writing alert profiles -- the paragraph that decides what a customer is
 * shown.
 *
 * Until this existed, onboarding a customer was a SQL insert run by whoever
 * held the service-role key (HANDOFF work-queue item 5). Every new account
 * signed up into an empty alerts panel and stayed there until someone did it
 * by hand.
 *
 * Same rules as `pipeline.ts`: `organization_id` is resolved from the
 * caller's own session, never supplied by the browser, and no write is
 * trusted until the row comes back -- a policy refusal is zero rows, not an
 * error.
 *
 * What this does NOT do is embed the text. The dashboard has no embedding
 * provider, and the vector must come from the same model as the corpus or the
 * similarity is noise (AGENTS.md, "Embeddings are not interchangeable"). A
 * new or edited profile is stored with a null embedding; the nightly sweep
 * embeds it and backfills its matches against every open tender. Migration
 * 0021 is what nulls the vector when the text changes.
 *
 * Profiles are deactivated, never deleted: `tender_matches` cascades on
 * delete, and a customer's record of what they were alerted to is not
 * something a settings screen should be able to erase in one click.
 */

import { revalidatePath } from "next/cache";

import { resolveOrganization } from "@/lib/organization";
import { isSupabaseConfigured } from "@/lib/supabase/config";
import { createClient } from "@/lib/supabase/server";

export type ProfileResult = { ok: true; id: string } | { ok: false; error: string };

export type ProfileInput = {
  id?: string;
  name: string;
  description: string;
  /** Comma- or newline-separated, as typed. */
  keywords: string;
  excludedKeywords: string;
  notifyEmail: boolean;
};

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Below this a description is a label, not a capability statement, and the
 *  embedding of a label matches everything a little and nothing well. */
const MIN_DESCRIPTION = 40;
const MAX_DESCRIPTION = 4000;
const MAX_KEYWORDS = 30;
const MAX_KEYWORD_LENGTH = 60;

/**
 * Split, trim, de-duplicate. `%` and `_` are removed because the matcher
 * applies keywords with ILIKE, where they are wildcards: a keyword of "_"
 * would match every tender, and an excluded keyword of "%" would veto them
 * all.
 */
function parseKeywords(raw: string, label: string): string[] | { error: string } {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const part of raw.split(/[,\n]/)) {
    const word = part.replace(/[%_]/g, " ").replace(/\s+/g, " ").trim();
    if (!word) continue;
    if (word.length > MAX_KEYWORD_LENGTH) {
      return { error: `${label}: "${word.slice(0, 20)}…" is longer than ${MAX_KEYWORD_LENGTH} characters.` };
    }
    const key = word.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(word);
  }
  if (out.length > MAX_KEYWORDS) return { error: `${label}: at most ${MAX_KEYWORDS}.` };
  return out;
}

export async function saveProfile(input: ProfileInput): Promise<ProfileResult> {
  if (!isSupabaseConfigured) {
    return { ok: false, error: "This build has no database configured." };
  }
  if (input.id !== undefined && !UUID.test(input.id)) {
    return { ok: false, error: "That profile id is not valid." };
  }

  const name = input.name.trim();
  const description = input.description.trim();
  if (!name || name.length > 120) {
    return { ok: false, error: "Give the profile a name of up to 120 characters." };
  }
  if (description.length < MIN_DESCRIPTION) {
    return {
      ok: false,
      error:
        `Describe what you build in at least ${MIN_DESCRIPTION} characters. ` +
        "This text is what tenders are matched against; a few words match everything a little and nothing well.",
    };
  }
  if (description.length > MAX_DESCRIPTION) {
    return { ok: false, error: `The description is limited to ${MAX_DESCRIPTION} characters.` };
  }

  const keywords = parseKeywords(input.keywords, "Keywords");
  if ("error" in keywords) return { ok: false, error: keywords.error };
  const excluded = parseKeywords(input.excludedKeywords, "Excluded keywords");
  if ("error" in excluded) return { ok: false, error: excluded.error };

  const fields = {
    name,
    description,
    keywords,
    excluded_keywords: excluded,
    notify_email: Boolean(input.notifyEmail),
  };

  const supabase = await createClient();

  let id: string | undefined;
  if (input.id) {
    const { data, error } = await supabase
      .from("alert_profiles")
      .update(fields)
      .eq("id", input.id)
      .select("id");
    if (error) return { ok: false, error: error.message };
    id = (data?.[0] as { id: string } | undefined)?.id;
    if (!id) return { ok: false, error: "That profile no longer exists in your organization." };
  } else {
    const org = await resolveOrganization(supabase);
    if ("error" in org) return { ok: false, error: org.error };

    const {
      data: { user },
    } = await supabase.auth.getUser();

    const { data, error } = await supabase
      .from("alert_profiles")
      .insert({ ...fields, organization_id: org.id, created_by: user?.id ?? null })
      .select("id");
    if (error) return { ok: false, error: error.message };
    id = (data?.[0] as { id: string } | undefined)?.id;
    if (!id) return { ok: false, error: "The profile could not be saved." };
  }

  revalidatePath("/profiles");
  revalidatePath("/");
  return { ok: true, id };
}

/** Pause or resume a profile. A paused profile keeps its matches and stops
 *  producing new ones. */
export async function setProfileActive(id: string, active: boolean): Promise<ProfileResult> {
  if (!isSupabaseConfigured) {
    return { ok: false, error: "This build has no database configured." };
  }
  if (!UUID.test(id)) return { ok: false, error: "That profile id is not valid." };

  const supabase = await createClient();
  const { data, error } = await supabase
    .from("alert_profiles")
    .update({ is_active: active })
    .eq("id", id)
    .select("id");

  if (error) return { ok: false, error: error.message };
  if (!data || data.length === 0) {
    return { ok: false, error: "That profile no longer exists in your organization." };
  }

  revalidatePath("/profiles");
  return { ok: true, id };
}
