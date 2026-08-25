import { Badge } from "@/components/ui/badge";
import { daysUntil } from "@/lib/utils";
import type { ExtractionStatus, PipelineStatus, TenderStatus } from "@/lib/types";

const TENDER_STATUS_VARIANT: Record<TenderStatus, "success" | "warning" | "secondary" | "info"> = {
  open: "success",
  closing_soon: "warning",
  closed: "secondary",
  awarded: "info",
  cancelled: "secondary",
};

const TENDER_STATUS_LABEL: Record<TenderStatus, string> = {
  open: "Open",
  closing_soon: "Closing soon",
  closed: "Closed",
  awarded: "Awarded",
  cancelled: "Cancelled",
};

export function TenderStatusBadge({ status }: { status: TenderStatus }) {
  return <Badge variant={TENDER_STATUS_VARIANT[status]}>{TENDER_STATUS_LABEL[status]}</Badge>;
}

const PIPELINE_LABEL: Record<PipelineStatus, string> = {
  new: "New",
  evaluating: "Evaluating",
  drafting: "Drafting",
  submitted: "Submitted",
  won: "Won",
  lost: "Lost",
  no_bid: "No bid",
};

export function PipelineStatusBadge({ status }: { status: PipelineStatus }) {
  const variant =
    status === "won" ? "success" : status === "lost" ? "danger" : status === "no_bid" ? "secondary" : "outline";
  return <Badge variant={variant}>{PIPELINE_LABEL[status]}</Badge>;
}

/**
 * Extraction provenance.
 *
 * Shown wherever a tender is, not tucked into a settings screen: a record the
 * pipeline could not validate must look different from one it could, or the
 * dashboard quietly presents unverified data as fact.
 */
export function ExtractionBadge({ status }: { status: ExtractionStatus }) {
  if (status === "extracted") return null;

  const copy: Record<Exclude<ExtractionStatus, "extracted">, { label: string; variant: "warning" | "secondary" | "danger" }> = {
    needs_review: { label: "Needs review", variant: "warning" },
    pending: { label: "Not yet extracted", variant: "secondary" },
    failed: { label: "Extraction failed", variant: "danger" },
  };

  const { label, variant } = copy[status];
  return <Badge variant={variant}>{label}</Badge>;
}

/** Deadline urgency. Colour carries the signal so the feed scans in one pass. */
export function DeadlineBadge({ deadline }: { deadline: string | null }) {
  const days = daysUntil(deadline);
  if (days === null) return <span className="text-sm text-slate-400">—</span>;

  const variant = days < 0 ? "secondary" : days <= 7 ? "danger" : days <= 21 ? "warning" : "outline";
  const label =
    days < 0 ? `Closed ${Math.abs(days)}d ago` : days === 0 ? "Today" : `${days}d left`;

  return <Badge variant={variant}>{label}</Badge>;
}
