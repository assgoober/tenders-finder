"use client";

/**
 * Human review queue.
 *
 * Why this screen exists. When extraction fails twice, or a safety classifier
 * declines the document, the pipeline stores the tender but deliberately does
 * not embed it — an unvalidated record must not enter the matching corpus and
 * raise alerts nobody checked (REPOMIX.md ADR-2). The consequence is that
 * these tenders raise no alert, score against no profile, and are easy to miss
 * in the feed. Without this queue the summary tile counts them and nothing
 * lets anyone act on them: a run that parked a third of its tenders looks
 * exactly like a run that worked.
 *
 * Two design consequences follow from that, and neither is cosmetic:
 *
 *   1. The original notice is the primary action, not a footnote. For these
 *      rows the portal is the only trustworthy source — the extracted fields
 *      are absent by design, so sending a reviewer anywhere else would be
 *      sending them to a blank record.
 *   2. A schema failure and a policy decline are separated. They look alike
 *      (empty record, human needed) and call for different responses: a
 *      schema failure is a document or prompt problem worth investigating,
 *      while a wall of declines is a policy boundary and no amount of
 *      re-running changes it.
 */

import { useMemo, useState } from "react";
import { AlertTriangle, ExternalLink, ShieldAlert } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import type { Tender } from "@/lib/types";
import { cn, formatDate } from "@/lib/utils";

type ReasonFilter = "all" | "policy" | "schema";

/** A decline is recorded as a category; anything else got this far by failing
 *  validation twice. Read from metadata rather than pattern-matching the error
 *  string, which is a human-readable message and free to change. */
function isPolicyDecline(tender: Tender): boolean {
  return tender.metadata.extraction?.refusal_category != null;
}

export function ReviewQueue({
  tenders,
  onSelect,
}: {
  tenders: Tender[];
  onSelect: (tender: Tender) => void;
}) {
  const [reason, setReason] = useState<ReasonFilter>("all");

  // `failed` belongs here too: it never reached validation (an empty document,
  // an API error), and it is just as invisible downstream as needs_review.
  const queue = useMemo(
    () =>
      tenders
        .filter((t) => t.extraction_status === "needs_review" || t.extraction_status === "failed")
        .sort((a, b) => {
          // Soonest deadline first — a parked tender that closes on Friday is
          // the one that costs something if it stays parked.
          const at = a.deadline ? new Date(a.deadline).getTime() : Number.POSITIVE_INFINITY;
          const bt = b.deadline ? new Date(b.deadline).getTime() : Number.POSITIVE_INFINITY;
          return at - bt;
        }),
    [tenders],
  );

  const policyCount = queue.filter(isPolicyDecline).length;
  const schemaCount = queue.length - policyCount;

  const filtered = queue.filter((t) => {
    if (reason === "policy") return isPolicyDecline(t);
    if (reason === "schema") return !isPolicyDecline(t);
    return true;
  });

  if (queue.length === 0) {
    return (
      <Card className="mt-4">
        <CardContent className="p-8 text-center">
          <p className="text-sm font-medium text-slate-900">Nothing waiting for review</p>
          <p className="mt-1 text-sm text-slate-500">
            Every tender ingested so far passed schema validation and was embedded.
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="mt-4 space-y-4">
      <div className="rounded-lg border border-amber-200 bg-amber-50 p-4">
        <p className="text-sm text-amber-900">
          <strong>{queue.length}</strong>{" "}
          {queue.length === 1 ? "tender is" : "tenders are"} stored but{" "}
          <strong>not embedded</strong>. They raise no alerts and match no profile until a
          human clears them. The original notice is the only reliable source for these.
        </p>
      </div>

      <div className="flex flex-wrap gap-2">
        <FilterButton active={reason === "all"} onClick={() => setReason("all")}>
          All {queue.length}
        </FilterButton>
        <FilterButton active={reason === "schema"} onClick={() => setReason("schema")}>
          Extraction failed {schemaCount}
        </FilterButton>
        <FilterButton active={reason === "policy"} onClick={() => setReason("policy")}>
          Declined by policy {policyCount}
        </FilterButton>
      </div>

      <ul className="space-y-3">
        {filtered.map((tender) => (
          <ReviewRow key={tender.id} tender={tender} onSelect={onSelect} />
        ))}
      </ul>
    </div>
  );
}

function ReviewRow({
  tender,
  onSelect,
}: {
  tender: Tender;
  onSelect: (tender: Tender) => void;
}) {
  const policy = isPolicyDecline(tender);
  const run = tender.metadata.extraction;

  return (
    <li>
      <Card>
        <CardContent className="p-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="font-medium text-slate-900">{tender.title}</p>
              <p className="mt-0.5 text-sm text-slate-500">
                {tender.buyer_name ?? "Buyer not stated"}
                {tender.metadata.reference_number && (
                  <>
                    {" · "}
                    <span className="font-mono text-xs">{tender.metadata.reference_number}</span>
                  </>
                )}
              </p>
            </div>

            <Badge variant={policy ? "outline" : "warning"} className="shrink-0 gap-1">
              {policy ? (
                <ShieldAlert className="h-3 w-3" aria-hidden="true" />
              ) : (
                <AlertTriangle className="h-3 w-3" aria-hidden="true" />
              )}
              {policy ? "Declined by policy" : "Extraction failed"}
            </Badge>
          </div>

          <dl className="mt-3 grid gap-x-6 gap-y-1 text-sm sm:grid-cols-2">
            <Row label="Deadline">
              {tender.deadline ? formatDate(tender.deadline) : "Not extracted"}
            </Row>
            <Row label="Published">
              {tender.publication_date ? formatDate(tender.publication_date) : "Not extracted"}
            </Row>
            <Row label="Attempts">{run ? String(run.attempts) : "—"}</Row>
            <Row label="Model">{tender.extraction_model ?? "—"}</Row>
          </dl>

          {/* The reason, verbatim. A reviewer deciding whether this is worth
              re-running needs the actual validation errors, not a summary of
              them — and a policy category is not something a rerun changes. */}
          {tender.extraction_error && (
            <details className="mt-3">
              <summary className="cursor-pointer text-sm font-medium text-slate-700">
                Why it is here
              </summary>
              <p className="mt-2 whitespace-pre-wrap break-words rounded-md bg-slate-50 p-3 font-mono text-xs text-slate-700">
                {tender.extraction_error}
              </p>
              {policy && (
                <p className="mt-2 text-xs text-slate-500">
                  The request was declined, and the server-side fallback declined it too.
                  It was not retried: the same prompt over the same document draws the same
                  decline.
                </p>
              )}
              {run?.truncated_input && (
                <p className="mt-2 text-xs text-amber-700">
                  The document exceeded the extraction ceiling and was truncated — some
                  content was never read.
                </p>
              )}
            </details>
          )}

          {tender.metadata.unclear_sections.length > 0 && (
            <ul className="mt-3 space-y-1 text-sm text-slate-600">
              {tender.metadata.unclear_sections.map((section) => (
                <li key={section}>• {section}</li>
              ))}
            </ul>
          )}

          <div className="mt-4 flex flex-wrap gap-2">
            {/* Primary, deliberately: for a record with no extracted fields,
                the notice is the only thing worth opening. */}
            <Button asChild size="sm">
              <a href={tender.source_url} target="_blank" rel="noopener noreferrer">
                <ExternalLink className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
                Open original notice
              </a>
            </Button>
            <Button variant="outline" size="sm" onClick={() => onSelect(tender)}>
              View stored record
            </Button>
          </div>
        </CardContent>
      </Card>
    </li>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-2">
      <dt className="text-slate-500">{label}</dt>
      <dd className="text-slate-900">{children}</dd>
    </div>
  );
}

function FilterButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        "rounded-full border px-3 py-1.5 text-sm font-medium transition-colors",
        active
          ? "border-slate-900 bg-slate-900 text-white"
          : "border-slate-200 bg-white text-slate-600 hover:bg-slate-50",
      )}
    >
      {children}
    </button>
  );
}
