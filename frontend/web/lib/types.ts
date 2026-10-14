/**
 * Domain types for the workspace.
 *
 * These mirror the database schema and the Pydantic extraction schema. The
 * PEGS body lives under `metadata` exactly as it does in the `tenders` JSONB
 * column, so the UI reads the same shape the extractor writes — no
 * translation layer to drift out of sync.
 */

export type TenderStatus = "open" | "closing_soon" | "closed" | "awarded" | "cancelled";

export type PipelineStatus =
  | "new"
  | "evaluating"
  | "drafting"
  | "submitted"
  | "won"
  | "lost"
  | "no_bid";

export type ExtractionStatus = "pending" | "extracted" | "needs_review" | "failed";

export interface Certification {
  name: string;
  issuing_body: string | null;
  mandatory: boolean | null;
}

/** PEGS — P */
export interface ParticipationConditions {
  eligibility_summary: string | null;
  mandatory_prerequisites: string[];
  required_certifications: Certification[];
  required_experience: string | null;
  financial_requirements: string | null;
  geographic_restrictions: string | null;
}

/** PEGS — E */
export interface EvaluationCriterion {
  criterion: string;
  weight_percent: number | null;
  description: string | null;
}

export interface EvaluationFramework {
  method: string | null;
  criteria: EvaluationCriterion[];
  price_weight_percent: number | null;
}

/** PEGS — G */
export interface LineItem {
  description: string;
  quantity: number | null;
  unit: string | null;
  part_number: string | null;
}

export interface TechnicalGuidelines {
  scope_summary: string | null;
  technical_specs: string[];
  referenced_standards: string[];
  line_items: LineItem[];
  minimum_order_quantity: number | null;
  delivery_terms: string | null;
  contract_duration: string | null;
  warranty_terms: string | null;
}

/** PEGS — S */
export interface SelectionMetrics {
  award_basis: string | null;
  decisive_metric: string | null;
  number_of_awards: number | null;
  framework_agreement: boolean | null;
}

export interface KeyDate {
  label: string;
  date: string | null;
}

/**
 * The `tenders.metadata` JSONB payload.
 *
 * `sector_specific` is the dual-use pressure valve: defense clearance levels
 * today, medical device classes tomorrow, with no type change here.
 */
/**
 * What the pipeline records about the extraction run itself, written to
 * `metadata.extraction`. Everything here is diagnostic — it says how the
 * fields above were arrived at, which is what a reviewer needs in order to
 * decide whether to trust them or open the original notice.
 */
export interface ExtractionRun {
  prompt_version: string;
  attempts: number;
  /** The document was longer than the extractor's ceiling and was cut. Real
   *  information loss, so it is recorded rather than done silently. */
  truncated_input: boolean;
  input_tokens: number | null;
  output_tokens: number | null;
  /** Policy category when a safety classifier declined the request, e.g.
   *  "cyber". Null on the ordinary path. A decline and a schema failure both
   *  land in `needs_review`, and this is what tells them apart. */
  refusal_category: string | null;
  /** Which model actually answered. Differs from the configured model when a
   *  fallback rescued a declined request. */
  served_by: string | null;
}

export interface TenderMetadata {
  participation: ParticipationConditions;
  evaluation: EvaluationFramework;
  guidelines: TechnicalGuidelines;
  selection: SelectionMetrics;
  key_dates: KeyDate[];
  sector_specific: Record<string, string | number | boolean | null>;
  reference_number: string | null;
  language: string | null;
  extraction_confidence: "high" | "medium" | "low";
  unclear_sections: string[];
  /** Absent on rows written before this field existed, so optional. */
  extraction?: ExtractionRun;
}

export interface Tender {
  id: string;
  title: string;
  summary: string | null;
  buyer_name: string | null;
  /** Link to the original notice. Never hide this — it is what makes the
   *  extraction auditable rather than something to be taken on faith. */
  source_url: string;
  source_name: string | null;
  publication_date: string | null;
  deadline: string | null;
  status: TenderStatus;
  estimated_value: number | null;
  currency: string | null;
  extraction_status: ExtractionStatus;
  /** Why the extraction did not complete. Null when it did. */
  extraction_error: string | null;
  extraction_model: string | null;
  metadata: TenderMetadata;
}

export interface TenderMatch {
  tender_id: string;
  profile_name: string;
  score: number;
  matched_keywords: string[];
}

export interface PipelineEntry {
  tender_id: string;
  status: PipelineStatus;
  owner: string | null;
  notes: string | null;
}

/**
 * Board columns, in bid-lifecycle order.
 *
 * Every `PipelineStatus` appears exactly once — a status missing from this
 * list would make its cards vanish from the board rather than sit in a
 * column, so the type below is annotated to keep the two in step.
 */
export const PIPELINE_COLUMNS: { status: PipelineStatus; label: string }[] = [
  { status: "new", label: "New" },
  { status: "evaluating", label: "Evaluating" },
  { status: "drafting", label: "Drafting Proposal" },
  { status: "submitted", label: "Submitted" },
  { status: "won", label: "Won" },
  { status: "lost", label: "Lost" },
  { status: "no_bid", label: "No bid" },
];
