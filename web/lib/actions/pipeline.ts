"use server";

/**
 * Writing to the bid pipeline.
 *
 * Server actions rather than client-side Supabase calls, for a reason that
 * matters more here than anywhere else in this app: `organization_id` is
 * never a value the browser supplies. It is resolved from the caller's own
 * session, so a tampered request cannot name someone else's organization.
 * RLS would refuse that row anyway — `tender_pipeline_rw` carries a WITH
 * CHECK, not only a USING — but the two together mean a mistake has to get
 * past both.
 *
 * None of these actions trusts its own success. A policy refusal arrives as
 * zero rows affected, not as an error, so a write that touched nothing looks
 * exactly like a write that worked. Every statement below asks for the row
 * back and reports failure when it does not arrive.
 *
 * `updated_at` is deliberately not set here: `tender_pipeline_set_updated_at`
 * already does it on every UPDATE, and a client-supplied timestamp would only
 * be overwritten by it.
 */

import { createClient } from "@/lib/supabase/server";
import { isSupabaseConfigured } from "@/lib/supabase/config";
import { resolveOrganization } from "@/lib/organization";
import { PIPELINE_COLUMNS, type PipelineStatus } from "@/lib/types";

export type ActionResult = { ok: true } | { ok: false; error: string };

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Every status the board can produce, taken from the column list itself so
 *  the two cannot drift apart. */
const STATUSES = new Set<string>(PIPELINE_COLUMNS.map((column) => column.status));


/** Guard shared by every action: a configured project and a well-formed id. */
function precheck(tenderId: string): string | null {
  if (!isSupabaseConfigured) {
    return "This build has no database configured — the pipeline is read-only demo data.";
  }
  if (!UUID.test(tenderId)) return "That tender id is not valid.";
  return null;
}

/**
 * Put a tender on the board.
 *
 * This is the step the product was missing outright: an alert said a tender
 * matched, and there was nowhere to go from it. The upsert is keyed on
 * `tender_pipeline_unique (organization_id, tender_id)` and ignores
 * duplicates, so tracking something twice is not an error and — more
 * importantly — does not drag a tender that already reached `submitted` back
 * to `new`.
 */
export async function trackTender(tenderId: string): Promise<ActionResult> {
  const bad = precheck(tenderId);
  if (bad) return { ok: false, error: bad };

  const supabase = await createClient();
  const org = await resolveOrganization(supabase);
  if ("error" in org) return { ok: false, error: org.error };

  const { error } = await supabase.from("tender_pipeline").upsert(
    { organization_id: org.id, tender_id: tenderId, status: "new" },
    { onConflict: "organization_id,tender_id", ignoreDuplicates: true },
  );

  if (error) return { ok: false, error: error.message };

  // `ignoreDuplicates` returns nothing on the duplicate path, and a policy
  // refusal returns nothing either, so success is confirmed by reading the
  // row back rather than inferred from the absence of an error.
  const { data: confirmed, error: readError } = await supabase
    .from("tender_pipeline")
    .select("tender_id")
    .eq("tender_id", tenderId)
    .maybeSingle();

  if (readError) return { ok: false, error: readError.message };
  if (!confirmed) {
    return { ok: false, error: "The tender could not be added to your pipeline." };
  }

  return { ok: true };
}

/**
 * Move a tender between columns.
 *
 * The status is checked here as well as by the enum in the database. Postgres
 * would reject a bad value regardless, but as `invalid input value for enum
 * pipeline_status` — a sentence written for whoever designed the schema, not
 * for whoever is using the board.
 */
export async function movePipelineEntry(
  tenderId: string,
  status: PipelineStatus,
): Promise<ActionResult> {
  const bad = precheck(tenderId);
  if (bad) return { ok: false, error: bad };
  if (!STATUSES.has(status)) return { ok: false, error: `"${status}" is not a pipeline stage.` };

  const supabase = await createClient();
  const { data, error } = await supabase
    .from("tender_pipeline")
    .update({ status })
    .eq("tender_id", tenderId)
    .select("tender_id");

  if (error) return { ok: false, error: error.message };
  if (!data || data.length === 0) {
    // Either the policy refused the row or the tender was never tracked.
    // Both are worth showing; neither should pass as a completed move.
    return { ok: false, error: "That tender is no longer on your board." };
  }

  return { ok: true };
}

/** Free-text notes on one entry. An empty string clears them. */
export async function savePipelineNotes(
  tenderId: string,
  notes: string,
): Promise<ActionResult> {
  const bad = precheck(tenderId);
  if (bad) return { ok: false, error: bad };

  const trimmed = notes.trim();
  if (trimmed.length > 4000) return { ok: false, error: "Notes are limited to 4,000 characters." };

  const supabase = await createClient();
  const { data, error } = await supabase
    .from("tender_pipeline")
    .update({ notes: trimmed || null })
    .eq("tender_id", tenderId)
    .select("tender_id");

  if (error) return { ok: false, error: error.message };
  if (!data || data.length === 0) {
    return { ok: false, error: "That tender is no longer on your board." };
  }

  return { ok: true };
}

/** Take a tender off the board. The tender itself is untouched — this row is
 *  this organization's decision to pursue it, not the notice. */
export async function untrackTender(tenderId: string): Promise<ActionResult> {
  const bad = precheck(tenderId);
  if (bad) return { ok: false, error: bad };

  const supabase = await createClient();
  const { data, error } = await supabase
    .from("tender_pipeline")
    .delete()
    .eq("tender_id", tenderId)
    .select("tender_id");

  if (error) return { ok: false, error: error.message };
  if (!data || data.length === 0) {
    return { ok: false, error: "That tender is no longer on your board." };
  }

  return { ok: true };
}
