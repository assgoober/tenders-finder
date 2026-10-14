"use client";

/**
 * Bid pipeline board.
 *
 * Tracks the organization's own bid lifecycle, which is deliberately separate
 * from the tender's published status: a tender stays `open` while this
 * organization is already `submitted` and a competitor has not started.
 *
 * Drag and drop is implemented with the native HTML5 API rather than a
 * library — the interaction is one card into one column, and a dependency
 * that ships its own sensor system and context provider is not worth it at
 * this size. Every card also carries a keyboard-accessible move control, so
 * the board is fully usable without dragging.
 *
 * The board holds no state of its own beyond the drag gesture. Entries and
 * the move handler come from `Workspace`, because the same rows are written
 * from the detail panel too, and two copies of the list would disagree the
 * moment a tender was tracked from anywhere but here.
 */

import { useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { PIPELINE_COLUMNS, type PipelineEntry, type PipelineStatus, type Tender } from "@/lib/types";
import { cn, deadlineLabel } from "@/lib/utils";

export function PipelineBoard({
  tenders,
  entries,
  onSelect,
  onMove,
  busy = false,
}: {
  tenders: Tender[];
  entries: PipelineEntry[];
  onSelect: (tender: Tender) => void;
  onMove: (tenderId: string, status: PipelineStatus) => void;
  /** A write is in flight. Cards stay readable but cannot be moved again
   *  until it settles, so a failed move rolls back to a known list. */
  busy?: boolean;
}) {
  const [draggingId, setDraggingId] = useState<string | null>(null);
  const [hoverColumn, setHoverColumn] = useState<PipelineStatus | null>(null);

  const tenderById = useMemo(
    () => new Map(tenders.map((tender) => [tender.id, tender])),
    [tenders],
  );

  return (
    <div className="overflow-x-auto pb-2">
      <div className="flex min-w-max gap-4">
        {PIPELINE_COLUMNS.map((column) => {
          const columnEntries = entries.filter((entry) => entry.status === column.status);

          return (
            <section
              key={column.status}
              onDragOver={(event) => {
                if (busy) return;
                event.preventDefault();
                setHoverColumn(column.status);
              }}
              onDragLeave={() => setHoverColumn((current) => (current === column.status ? null : current))}
              onDrop={(event) => {
                event.preventDefault();
                const tenderId = event.dataTransfer.getData("text/plain") || draggingId;
                // Dropping a card back where it started is a no-op, not a write.
                const entry = tenderId ? entries.find((e) => e.tender_id === tenderId) : undefined;
                if (tenderId && entry && entry.status !== column.status) {
                  onMove(tenderId, column.status);
                }
                setDraggingId(null);
                setHoverColumn(null);
              }}
              className={cn(
                "flex w-72 flex-col rounded-lg border bg-slate-50/60 transition-colors",
                hoverColumn === column.status
                  ? "border-slate-400 bg-slate-100"
                  : "border-slate-200",
              )}
            >
              <header className="flex items-center justify-between px-3 py-2.5">
                <h3 className="text-sm font-medium text-slate-700">{column.label}</h3>
                <Badge variant="secondary">{columnEntries.length}</Badge>
              </header>

              <div className="flex flex-1 flex-col gap-2 px-2 pb-3">
                {columnEntries.map((entry) => {
                  const tender = tenderById.get(entry.tender_id);
                  // A tracked tender outside the loaded window — the feed is
                  // capped at 500 rows. Skipping it keeps the board honest
                  // rather than rendering a card with no title.
                  if (!tender) return null;

                  return (
                    <article
                      key={entry.tender_id}
                      draggable={!busy}
                      onDragStart={(event) => {
                        event.dataTransfer.setData("text/plain", entry.tender_id);
                        event.dataTransfer.effectAllowed = "move";
                        setDraggingId(entry.tender_id);
                      }}
                      onDragEnd={() => setDraggingId(null)}
                      onClick={() => onSelect(tender)}
                      className={cn(
                        "cursor-pointer rounded-md border border-slate-200 bg-white p-3 shadow-sm transition-shadow hover:shadow",
                        draggingId === entry.tender_id && "opacity-50",
                      )}
                    >
                      <p className="line-clamp-2 text-sm font-medium text-slate-900">
                        {tender.title}
                      </p>
                      <p className="mt-1 text-xs text-slate-500">
                        {tender.buyer_name ?? "Unknown buyer"}
                      </p>

                      <div className="mt-2 flex items-center justify-between gap-2">
                        <span className="text-xs text-slate-500">
                          {deadlineLabel(tender.deadline)}
                        </span>
                        {entry.owner && (
                          <Badge variant="outline" className="text-[10px]">
                            {entry.owner}
                          </Badge>
                        )}
                      </div>

                      {entry.notes && (
                        <p className="mt-2 border-t border-slate-100 pt-2 text-xs text-slate-500">
                          {entry.notes}
                        </p>
                      )}

                      <label className="mt-2 block" onClick={(event) => event.stopPropagation()}>
                        <span className="sr-only">Move {tender.title} to another stage</span>
                        <select
                          value={entry.status}
                          disabled={busy}
                          onChange={(event) => onMove(entry.tender_id, event.target.value as PipelineStatus)}
                          className="w-full rounded border border-slate-200 bg-white px-2 py-1 text-xs text-slate-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-400 disabled:opacity-60"
                        >
                          {PIPELINE_COLUMNS.map((option) => (
                            <option key={option.status} value={option.status}>
                              {option.label}
                            </option>
                          ))}
                        </select>
                      </label>
                    </article>
                  );
                })}

                {columnEntries.length === 0 && (
                  <p className="px-2 py-6 text-center text-xs text-slate-400">Nothing here</p>
                )}
              </div>
            </section>
          );
        })}
      </div>
    </div>
  );
}
