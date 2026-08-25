"use client";

/**
 * Tender detail — the full PEGS extraction, next to the original source.
 *
 * The source link sits in the header, permanently visible rather than at the
 * bottom of a scroll. Everything below it is machine-extracted, and in a
 * defense procurement decision a team has to be able to check it against the
 * document in one click. That verifiability is what earns trust in the
 * extraction; hiding the link would quietly ask users to take it on faith.
 */

import { ExternalLink, X } from "lucide-react";

import { ExtractionBadge, TenderStatusBadge } from "@/components/status-badges";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import type { Tender } from "@/lib/types";
import { formatCurrency, formatDate } from "@/lib/utils";

/**
 * Renders as a slide-over panel when `onClose` is given, and as a plain
 * inline document when it is not — the deep-link page at /tenders/[id] shows
 * the same extraction without an overlay to dismiss.
 */
export function TenderDetail({ tender, onClose }: { tender: Tender; onClose?: () => void }) {
  const { metadata } = tender;
  const isPanel = Boolean(onClose);

  const body = (
    <>
      <header
        className={
          isPanel
            ? "sticky top-0 z-10 border-b border-slate-200 bg-white px-6 py-4"
            : "border-b border-slate-200 bg-white px-6 py-4"
        }
      >
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <TenderStatusBadge status={tender.status} />
                <ExtractionBadge status={tender.extraction_status} />
                {metadata.reference_number && (
                  <span className="text-xs text-slate-500">{metadata.reference_number}</span>
                )}
              </div>
              <h2 className="mt-2 text-lg font-semibold text-slate-900">{tender.title}</h2>
              <p className="text-sm text-slate-500">{tender.buyer_name ?? "Unknown buyer"}</p>
            </div>
            {onClose && (
              <Button variant="ghost" size="icon" onClick={onClose} aria-label="Close">
                <X className="h-4 w-4" />
              </Button>
            )}
          </div>

          <div className="mt-4 flex flex-wrap items-center gap-3">
            <Button asChild size="sm">
              <a href={tender.source_url} target="_blank" rel="noreferrer noopener">
                Open original notice <ExternalLink className="h-3.5 w-3.5" />
              </a>
            </Button>
            <span className="text-xs text-slate-500">
              {tender.source_name ?? "Source portal"} · published {formatDate(tender.publication_date)}
            </span>
          </div>
        </header>

        <div className="space-y-6 px-6 py-5">
          {tender.extraction_status === "needs_review" && (
            <div className="rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
              <strong className="font-medium">Awaiting human review.</strong> Automated extraction
              could not validate this document, so no fields were saved. Read the original notice
              before acting on this tender.
            </div>
          )}

          <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <Fact label="Deadline" value={formatDate(tender.deadline)} />
            <Fact label="Published" value={formatDate(tender.publication_date)} />
            <Fact
              label="Estimated value"
              value={formatCurrency(tender.estimated_value, tender.currency)}
            />
            <Fact
              label="Extraction confidence"
              value={metadata.extraction_confidence ?? "—"}
            />
          </dl>

          {tender.summary && <p className="text-sm leading-relaxed text-slate-700">{tender.summary}</p>}

          {/* Tabs follow PEGS order — the same order the extraction prompt
              walks the document in, so the UI and the pipeline tell one story. */}
          <Tabs defaultValue="participation">
            <TabsList>
              <TabsTrigger value="participation">Participation</TabsTrigger>
              <TabsTrigger value="evaluation">Evaluation</TabsTrigger>
              <TabsTrigger value="guidelines">Guidelines</TabsTrigger>
              <TabsTrigger value="selection">Selection</TabsTrigger>
            </TabsList>

            <TabsContent value="participation">
              <Section title="Eligibility">
                <Prose value={metadata.participation.eligibility_summary} />
              </Section>
              <Section title="Mandatory prerequisites">
                <BulletList items={metadata.participation.mandatory_prerequisites} />
              </Section>
              <Section title="Required certifications">
                {metadata.participation.required_certifications.length ? (
                  <ul className="flex flex-wrap gap-2">
                    {metadata.participation.required_certifications.map((cert) => (
                      <li key={cert.name}>
                        <Badge variant={cert.mandatory ? "danger" : "secondary"}>
                          {cert.name}
                          {cert.mandatory === false ? " (advantage)" : ""}
                        </Badge>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <Empty />
                )}
              </Section>
              <Section title="Experience">
                <Prose value={metadata.participation.required_experience} />
              </Section>
              <Section title="Financial requirements">
                <Prose value={metadata.participation.financial_requirements} />
              </Section>
              <Section title="Geographic restrictions">
                <Prose value={metadata.participation.geographic_restrictions} />
              </Section>
            </TabsContent>

            <TabsContent value="evaluation">
              <Section title="Method">
                <Prose value={metadata.evaluation.method} />
              </Section>
              <Section title="Weighted criteria">
                {metadata.evaluation.criteria.length ? (
                  <ul className="space-y-2">
                    {metadata.evaluation.criteria.map((criterion) => (
                      <li key={criterion.criterion} className="space-y-1">
                        <div className="flex items-center justify-between gap-3 text-sm">
                          <span className="text-slate-700">{criterion.criterion}</span>
                          <span className="tabular-nums text-slate-500">
                            {criterion.weight_percent === null
                              ? "not stated"
                              : `${criterion.weight_percent}%`}
                          </span>
                        </div>
                        {criterion.weight_percent !== null && (
                          <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-100">
                            <div
                              className="h-full rounded-full bg-slate-900"
                              style={{ width: `${criterion.weight_percent}%` }}
                            />
                          </div>
                        )}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <Empty />
                )}
              </Section>
            </TabsContent>

            <TabsContent value="guidelines">
              <Section title="Scope">
                <Prose value={metadata.guidelines.scope_summary} />
              </Section>
              <Section title="Referenced standards">
                {metadata.guidelines.referenced_standards.length ? (
                  <ul className="flex flex-wrap gap-2">
                    {metadata.guidelines.referenced_standards.map((standard) => (
                      <li key={standard}>
                        {/* Monospace: these are matched character for character
                            downstream, so they are read as identifiers. */}
                        <Badge variant="outline" className="font-mono">
                          {standard}
                        </Badge>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <Empty />
                )}
              </Section>
              <Section title="Technical specifications">
                <BulletList items={metadata.guidelines.technical_specs} />
              </Section>
              <Section title="Line items">
                {metadata.guidelines.line_items.length ? (
                  <div className="overflow-x-auto rounded-md border border-slate-200">
                    <table className="w-full min-w-[32rem] text-left text-sm">
                      <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
                        <tr>
                          <th className="px-3 py-2 font-medium">Description</th>
                          <th className="px-3 py-2 font-medium">Qty</th>
                          <th className="px-3 py-2 font-medium">Unit</th>
                          <th className="px-3 py-2 font-medium">Part no.</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-100">
                        {metadata.guidelines.line_items.map((item, index) => (
                          <tr key={`${item.description}-${index}`}>
                            <td className="px-3 py-2 text-slate-700">{item.description}</td>
                            <td className="px-3 py-2 tabular-nums text-slate-700">
                              {item.quantity ?? "—"}
                            </td>
                            <td className="px-3 py-2 text-slate-500">{item.unit ?? "—"}</td>
                            <td className="px-3 py-2 font-mono text-xs text-slate-500">
                              {item.part_number ?? "—"}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <Empty />
                )}
              </Section>
              <div className="grid gap-4 sm:grid-cols-2">
                <Section title="Minimum order quantity">
                  <Prose
                    value={
                      metadata.guidelines.minimum_order_quantity === null
                        ? null
                        : String(metadata.guidelines.minimum_order_quantity)
                    }
                  />
                </Section>
                <Section title="Delivery terms">
                  <Prose value={metadata.guidelines.delivery_terms} />
                </Section>
                <Section title="Contract duration">
                  <Prose value={metadata.guidelines.contract_duration} />
                </Section>
                <Section title="Warranty">
                  <Prose value={metadata.guidelines.warranty_terms} />
                </Section>
              </div>
            </TabsContent>

            <TabsContent value="selection">
              <Section title="Award basis">
                <Prose value={metadata.selection.award_basis} />
              </Section>
              <Section title="Decisive metric">
                <Prose value={metadata.selection.decisive_metric} />
              </Section>
              <div className="grid gap-4 sm:grid-cols-2">
                <Section title="Number of awards">
                  <Prose
                    value={
                      metadata.selection.number_of_awards === null
                        ? null
                        : String(metadata.selection.number_of_awards)
                    }
                  />
                </Section>
                <Section title="Framework agreement">
                  <Prose
                    value={
                      metadata.selection.framework_agreement === null
                        ? null
                        : metadata.selection.framework_agreement
                          ? "Yes"
                          : "No"
                    }
                  />
                </Section>
              </div>
            </TabsContent>
          </Tabs>

          {metadata.key_dates.length > 0 && (
            <Section title="Key dates">
              <ul className="space-y-1 text-sm">
                {metadata.key_dates.map((entry) => (
                  <li key={entry.label} className="flex justify-between gap-4">
                    <span className="text-slate-700">{entry.label}</span>
                    <span className="text-slate-500">{formatDate(entry.date)}</span>
                  </li>
                ))}
              </ul>
            </Section>
          )}

          {Object.keys(metadata.sector_specific).length > 0 && (
            <Section title="Sector-specific">
              {/* Rendered generically from key/value pairs, so a new vertical
                  needs no UI change (REPOMIX.md ADR-1). */}
              <dl className="grid gap-2 sm:grid-cols-2">
                {Object.entries(metadata.sector_specific).map(([key, value]) => (
                  <div key={key} className="rounded-md border border-slate-200 px-3 py-2">
                    <dt className="text-xs uppercase tracking-wide text-slate-500">
                      {key.replace(/_/g, " ")}
                    </dt>
                    <dd className="text-sm text-slate-800">{String(value)}</dd>
                  </div>
                ))}
              </dl>
            </Section>
          )}

          {metadata.unclear_sections.length > 0 && (
            <Section title="Flagged by the extractor as unclear">
              <BulletList items={metadata.unclear_sections} />
            </Section>
          )}
      </div>
    </>
  );

  if (!onClose) {
    return (
      <article className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
        {body}
      </article>
    );
  }

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-slate-900/40" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-label={tender.title}
        className="h-full w-full max-w-3xl overflow-y-auto bg-white shadow-xl"
        onClick={(event) => event.stopPropagation()}
      >
        {body}
      </div>
    </div>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-slate-500">{label}</dt>
      <dd className="mt-0.5 text-sm font-medium text-slate-900">{value}</dd>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mt-4 first:mt-0">
      <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">{title}</h3>
      {children}
    </section>
  );
}

/**
 * Renders a nullable extracted field.
 *
 * A null means the source document did not state it — which is different from
 * the extractor failing. Saying so explicitly stops readers from inferring
 * that no requirement exists.
 */
function Prose({ value }: { value: string | null }) {
  if (!value) return <Empty />;
  return <p className="text-sm leading-relaxed text-slate-700">{value}</p>;
}

function BulletList({ items }: { items: string[] }) {
  if (!items.length) return <Empty />;
  return (
    <ul className="list-disc space-y-1 pl-5 text-sm leading-relaxed text-slate-700">
      {items.map((item, index) => (
        <li key={`${item.slice(0, 24)}-${index}`}>{item}</li>
      ))}
    </ul>
  );
}

function Empty() {
  return <p className="text-sm italic text-slate-400">Not stated in the source document</p>;
}
