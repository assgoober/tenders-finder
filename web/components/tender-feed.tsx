"use client";

/**
 * Main tender feed.
 *
 * The cognitive-load brief drives the layout: one row per tender, four
 * scannable columns, and filters that narrow rather than reconfigure. Deadline
 * pressure and match score are the two things a BD lead scans for, so they
 * are the two things carried by colour.
 */

import { useMemo, useState } from "react";
import { ExternalLink, Search } from "lucide-react";

import { DeadlineBadge, ExtractionBadge, TenderStatusBadge } from "@/components/status-badges";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { Tender, TenderMatch, TenderStatus } from "@/lib/types";
import { cn, formatCurrency, formatDate } from "@/lib/utils";

type DeadlineFilter = "all" | "7" | "30" | "open";

const STATUS_FILTERS: { value: TenderStatus | "all"; label: string }[] = [
  { value: "all", label: "All" },
  { value: "open", label: "Open" },
  { value: "closing_soon", label: "Closing soon" },
  { value: "closed", label: "Closed" },
];

const DEADLINE_FILTERS: { value: DeadlineFilter; label: string }[] = [
  { value: "all", label: "Any deadline" },
  { value: "7", label: "Next 7 days" },
  { value: "30", label: "Next 30 days" },
  { value: "open", label: "Not yet closed" },
];

export function TenderFeed({
  tenders,
  matches,
  onSelect,
}: {
  tenders: Tender[];
  matches: TenderMatch[];
  onSelect: (tender: Tender) => void;
}) {
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState<TenderStatus | "all">("all");
  const [deadline, setDeadline] = useState<DeadlineFilter>("all");

  const matchByTender = useMemo(
    () => new Map(matches.map((match) => [match.tender_id, match])),
    [matches],
  );

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const now = Date.now();

    return tenders.filter((tender) => {
      if (status !== "all" && tender.status !== status) return false;

      if (deadline !== "all") {
        if (!tender.deadline) return false;
        const delta = new Date(tender.deadline).getTime() - now;
        if (deadline === "open" && delta < 0) return false;
        if (deadline === "7" && (delta < 0 || delta > 7 * 86_400_000)) return false;
        if (deadline === "30" && (delta < 0 || delta > 30 * 86_400_000)) return false;
      }

      if (!needle) return true;
      // Search covers the buyer and the referenced standards too: "MIL-STD-810H"
      // is a realistic thing to type, and it lives in the extraction, not the title.
      const haystack = [
        tender.title,
        tender.summary ?? "",
        tender.buyer_name ?? "",
        ...tender.metadata.guidelines.referenced_standards,
      ]
        .join(" ")
        .toLowerCase();
      return haystack.includes(needle);
    });
  }, [tenders, query, status, deadline]);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative min-w-[16rem] flex-1">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search title, buyer, or standard (e.g. MIL-STD-810H)"
            className="h-9 w-full rounded-md border border-slate-200 bg-white pl-9 pr-3 text-sm placeholder:text-slate-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-400"
          />
        </div>

        <FilterGroup
          options={STATUS_FILTERS}
          value={status}
          onChange={(value) => setStatus(value as TenderStatus | "all")}
        />
        <FilterGroup
          options={DEADLINE_FILTERS}
          value={deadline}
          onChange={(value) => setDeadline(value as DeadlineFilter)}
        />
      </div>

      <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
        <table className="w-full min-w-[56rem] text-left text-sm">
          <thead className="border-b border-slate-200 bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
            <tr>
              <th className="px-4 py-3 font-medium">Tender</th>
              <th className="px-4 py-3 font-medium">Status</th>
              <th className="px-4 py-3 font-medium">Deadline</th>
              <th className="px-4 py-3 font-medium">Value</th>
              <th className="px-4 py-3 font-medium">Match</th>
              <th className="px-4 py-3" />
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {filtered.map((tender) => {
              const match = matchByTender.get(tender.id);
              return (
                <tr
                  key={tender.id}
                  onClick={() => onSelect(tender)}
                  className="cursor-pointer transition-colors hover:bg-slate-50"
                >
                  <td className="max-w-md px-4 py-3">
                    <div className="flex items-center gap-2">
                      <span className="font-medium text-slate-900">{tender.title}</span>
                      <ExtractionBadge status={tender.extraction_status} />
                    </div>
                    <div className="mt-0.5 text-xs text-slate-500">
                      {tender.buyer_name ?? "Unknown buyer"}
                      {tender.metadata.reference_number
                        ? ` · ${tender.metadata.reference_number}`
                        : ""}
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <TenderStatusBadge status={tender.status} />
                  </td>
                  <td className="whitespace-nowrap px-4 py-3">
                    <DeadlineBadge deadline={tender.deadline} />
                    <div className="mt-0.5 text-xs text-slate-500">
                      {formatDate(tender.deadline)}
                    </div>
                  </td>
                  <td className="whitespace-nowrap px-4 py-3 text-slate-700">
                    {formatCurrency(tender.estimated_value, tender.currency)}
                  </td>
                  <td className="px-4 py-3">
                    {match ? (
                      <div className="flex flex-col gap-1">
                        <Badge variant={match.score >= 0.85 ? "success" : "info"}>
                          {Math.round(match.score * 100)}%
                        </Badge>
                        <span className="text-xs text-slate-500">{match.profile_name}</span>
                      </div>
                    ) : (
                      <span className="text-xs text-slate-400">—</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-right">
                    {/* Route to the source, not into a modal: the link out is
                        what makes the extraction auditable. */}
                    <Button
                      asChild
                      variant="ghost"
                      size="sm"
                      onClick={(event) => event.stopPropagation()}
                    >
                      <a href={tender.source_url} target="_blank" rel="noreferrer noopener">
                        Source <ExternalLink className="h-3 w-3" />
                      </a>
                    </Button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>

        {filtered.length === 0 && (
          <p className="px-4 py-10 text-center text-sm text-slate-500">
            No tenders match these filters.
          </p>
        )}
      </div>

      <p className="text-xs text-slate-500">
        Showing {filtered.length} of {tenders.length} tenders.
      </p>
    </div>
  );
}

function FilterGroup<T extends string>({
  options,
  value,
  onChange,
}: {
  options: { value: T; label: string }[];
  value: T;
  onChange: (value: T) => void;
}) {
  return (
    <div className="flex rounded-md border border-slate-200 bg-white p-0.5">
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          onClick={() => onChange(option.value)}
          className={cn(
            "rounded px-2.5 py-1 text-xs font-medium transition-colors",
            value === option.value
              ? "bg-slate-900 text-white"
              : "text-slate-600 hover:bg-slate-100",
          )}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}
