/**
 * Live data access.
 *
 * Every query here runs through the caller's own session (see
 * `lib/supabase/server.ts`), so row level security decides what comes back and
 * none of these functions takes — or accepts — an organization id. That is not
 * a convenience: an organization filter written in TypeScript is a filter that
 * can be forgotten, and the tables it would guard hold competing defence
 * contractors' bid pipelines.
 *
 * The row → domain mapping lives here rather than in components, because the
 * database and the UI legitimately disagree in three places:
 *   - `tenders` has no `source_name`; the portal's display name lives on
 *     `tender_sources` and is joined in.
 *   - `tender_matches.match_reason` is a JSONB blob that already carries
 *     `profile_name` and `matched_keywords`, written by the SQL matcher.
 *   - `tender_pipeline.owner_user_id` is a uuid, and the UI wants a person.
 *     There is no table mapping one to the other yet, so this returns null
 *     rather than rendering a uuid where a name belongs.
 */

import { createClient } from "@/lib/supabase/server";
import type {
  ExtractionStatus,
  PipelineEntry,
  PipelineStatus,
  Tender,
  TenderMatch,
  TenderMetadata,
  TenderStatus,
} from "@/lib/types";

/**
 * Fill a partial `metadata` payload out to the full shape.
 *
 * A tender that is still `pending`, or that failed extraction, is stored with
 * an empty or partial `metadata` — that is the normal path, not corruption.
 * The detail view reads several levels deep into this object, so without a
 * normaliser the review queue would crash on exactly the rows it exists to
 * show.
 */
function normalizeMetadata(raw: unknown): TenderMetadata {
  const m = (raw ?? {}) as Partial<TenderMetadata>;
  return {
    participation: {
      eligibility_summary: null,
      mandatory_prerequisites: [],
      required_certifications: [],
      required_experience: null,
      financial_requirements: null,
      geographic_restrictions: null,
      ...(m.participation ?? {}),
    },
    evaluation: {
      method: null,
      criteria: [],
      price_weight_percent: null,
      ...(m.evaluation ?? {}),
    },
    guidelines: {
      scope_summary: null,
      technical_specs: [],
      referenced_standards: [],
      line_items: [],
      minimum_order_quantity: null,
      delivery_terms: null,
      contract_duration: null,
      warranty_terms: null,
      ...(m.guidelines ?? {}),
    },
    selection: {
      award_basis: null,
      decisive_metric: null,
      number_of_awards: null,
      framework_agreement: null,
      ...(m.selection ?? {}),
    },
    key_dates: m.key_dates ?? [],
    sector_specific: m.sector_specific ?? {},
    reference_number: m.reference_number ?? null,
    language: m.language ?? null,
    extraction_confidence: m.extraction_confidence ?? "low",
    unclear_sections: m.unclear_sections ?? [],
    extraction: m.extraction,
  };
}

interface TenderRow {
  id: string;
  title: string;
  summary: string | null;
  buyer_name: string | null;
  source_url: string;
  publication_date: string | null;
  deadline: string | null;
  status: TenderStatus;
  estimated_value: string | number | null;
  currency: string | null;
  extraction_status: ExtractionStatus;
  extraction_error: string | null;
  extraction_model: string | null;
  metadata: unknown;
  tender_sources: { display_name: string | null } | null;
}

function toTender(row: TenderRow): Tender {
  return {
    id: row.id,
    title: row.title,
    summary: row.summary,
    buyer_name: row.buyer_name,
    source_url: row.source_url,
    source_name: row.tender_sources?.display_name ?? null,
    publication_date: row.publication_date,
    deadline: row.deadline,
    status: row.status,
    // numeric arrives as a string from PostgREST — it does not fit a double
    // without loss, so the driver refuses to guess. Parsing here keeps the
    // formatting helpers working on a number.
    estimated_value: row.estimated_value == null ? null : Number(row.estimated_value),
    currency: row.currency,
    extraction_status: row.extraction_status,
    extraction_error: row.extraction_error,
    extraction_model: row.extraction_model,
    metadata: normalizeMetadata(row.metadata),
  };
}

const TENDER_COLUMNS =
  "id,title,summary,buyer_name,source_url,publication_date,deadline,status," +
  "estimated_value,currency,extraction_status,extraction_error,extraction_model," +
  "metadata,tender_sources(display_name)";

/**
 * How much of the corpus the workspace loads, and in what shape.
 *
 * The feed used to be one query: soonest deadline first, 500 rows. That was
 * correct while the corpus was a few hundred tenders and most of them were
 * still open. At 5,631 rows it became the opposite of correct -- the 500
 * earliest deadlines were ALL in the past, so the dashboard fetched the 500
 * most expired tenders in the database and nothing else.
 *
 * Measured on 2026-09-18, the first day anyone signed in to the deployed app:
 * open tenders inside that window, 0. Open tenders in the database, 4,786.
 * The tile read "Open tenders: 0", which was true of what had been fetched
 * and false about the world, and nothing anywhere raised an error. A bid team
 * was being shown precisely the tenders it could no longer bid on.
 *
 * So the window is now anchored on what is still biddable rather than on what
 * sorts first, and it is assembled from three pieces:
 */
/** Still open, soonest deadline first. This is the product. */
const OPEN_LIMIT = 400;
/** Most recently closed. Enough for the Closed filter to mean something,
 *  not enough to bury the tenders somebody can still win. */
const CLOSED_LIMIT = 100;

const OPEN_STATUSES = ["open", "closing_soon"];
const CLOSED_STATUSES = ["closed", "awarded", "cancelled"];

export async function fetchTenders(): Promise<Tender[]> {
  return fetchFeed([]);
}

/**
 * The feed, plus any tender the rest of the workspace needs regardless.
 *
 * `required` carries the tenders referenced by a live match or a pipeline
 * entry. Without it the alerts panel silently drops alerts whose tender falls
 * outside the window -- which is exactly what happened: 42 alerts were
 * delivered by email that morning for tenders closing the following week, and
 * the panel showed 13, every one of them already expired, because only those
 * 13 happened to sort into the first 500 rows. An alert the product emailed
 * and then cannot display is worse than one it never sent.
 */
async function fetchFeed(required: string[]): Promise<Tender[]> {
  const supabase = await createClient();

  const [open, closed] = await Promise.all([
    supabase
      .from("tenders")
      .select(TENDER_COLUMNS)
      .in("status", OPEN_STATUSES)
      // Soonest deadline first, with undated tenders last rather than first —
      // a null deadline is "unknown", not "urgent".
      .order("deadline", { ascending: true, nullsFirst: false })
      .limit(OPEN_LIMIT),
    supabase
      .from("tenders")
      .select(TENDER_COLUMNS)
      .in("status", CLOSED_STATUSES)
      // Descending here, deliberately: the useful closed tender is the one
      // that closed yesterday, not the one that closed in 2023.
      .order("deadline", { ascending: false, nullsFirst: false })
      .limit(CLOSED_LIMIT),
  ]);

  if (open.error) throw new Error(`fetchTenders (open): ${open.error.message}`);
  if (closed.error) throw new Error(`fetchTenders (closed): ${closed.error.message}`);

  const rows = [...(open.data ?? []), ...(closed.data ?? [])];
  const seen = new Set(rows.map((r) => (r as unknown as TenderRow).id));

  const missing = required.filter((id) => !seen.has(id));
  if (missing.length > 0) {
    const extra = await supabase.from("tenders").select(TENDER_COLUMNS).in("id", missing);
    if (extra.error) throw new Error(`fetchTenders (referenced): ${extra.error.message}`);
    rows.push(...(extra.data ?? []));
  }

  return rows.map((row) => toTender(row as unknown as TenderRow));
}

/** One tender by id, or null when it does not exist or RLS hides it. */
export async function fetchTender(id: string): Promise<Tender | null> {
  const supabase = await createClient();
  const { data, error } = await supabase
    .from("tenders")
    .select(TENDER_COLUMNS)
    .eq("id", id)
    .maybeSingle();

  if (error) throw new Error(`fetchTender: ${error.message}`);
  return data ? toTender(data as unknown as TenderRow) : null;
}

interface MatchRow {
  tender_id: string;
  score: number;
  match_reason: { profile_name?: string; matched_keywords?: string[] } | null;
}

export async function fetchMatches(): Promise<TenderMatch[]> {
  const supabase = await createClient();
  const { data, error } = await supabase
    .from("tender_matches")
    .select("tender_id,score,match_reason")
    // A dismissed match is a decision someone already made. Re-raising it
    // would train people to ignore the alerts panel.
    .is("dismissed_at", null)
    .order("score", { ascending: false })
    .limit(200);

  if (error) throw new Error(`fetchMatches: ${error.message}`);
  return (data ?? []).map((row) => {
    const r = row as unknown as MatchRow;
    return {
      tender_id: r.tender_id,
      profile_name: r.match_reason?.profile_name ?? "Unnamed profile",
      score: r.score,
      matched_keywords: r.match_reason?.matched_keywords ?? [],
    };
  });
}

interface PipelineRow {
  tender_id: string;
  status: PipelineStatus;
  notes: string | null;
}

export async function fetchPipeline(): Promise<PipelineEntry[]> {
  const supabase = await createClient();
  const { data, error } = await supabase
    .from("tender_pipeline")
    .select("tender_id,status,notes")
    .order("position", { ascending: true })
    .limit(500);

  if (error) throw new Error(`fetchPipeline: ${error.message}`);
  return (data ?? []).map((row) => {
    const r = row as unknown as PipelineRow;
    return {
      tender_id: r.tender_id,
      status: r.status,
      // See the note at the top: owner_user_id is a uuid and there is no
      // name to resolve it to yet.
      owner: null,
      notes: r.notes,
    };
  });
}

export async function fetchWorkspace() {
  // Matches and pipeline first, because what they reference decides which
  // tenders the feed must carry no matter where they sort.
  const [matches, pipeline] = await Promise.all([fetchMatches(), fetchPipeline()]);

  const required = Array.from(
    new Set([
      ...matches.map((m) => m.tender_id),
      ...pipeline.map((p) => p.tender_id),
    ]),
  );

  const tenders = await fetchFeed(required);
  return { tenders, matches, pipeline };
}
