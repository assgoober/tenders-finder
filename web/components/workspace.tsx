"use client";

/**
 * Unified workspace.
 *
 * One screen replaces the portal-by-portal routine: everything relevant is
 * here, and the only reason to leave is to verify a tender against its
 * original notice.
 *
 * Cognitive-load decisions, all deliberate:
 *   - Three views, not seven. Feed answers "what is out there"; Pipeline
 *     answers "what are we doing about it"; Review answers "what did the
 *     machine refuse to answer". Nothing else earns a tab.
 *   - Four summary figures, chosen because each one can change what a BD
 *     lead does this morning.
 *   - Detail opens in a side panel, so the feed and its filters stay put.
 *
 * The review tab earns its place because its contents are invisible
 * everywhere else: a tender that failed extraction is deliberately left
 * unembedded, so it raises no alert and matches no profile. Counting it in a
 * tile without offering anywhere to go was the gap this closes.
 *
 * This component owns the pipeline rows because two surfaces write them — the
 * board and the detail panel — and the summary tiles read them. Held lower
 * down, a tender tracked from an alert would not appear on the board until a
 * reload, which reads as the click having failed.
 */

import { useState } from "react";

import { AlertsPanel } from "@/components/alerts-panel";
import { PipelineBoard } from "@/components/pipeline-board";
import { ReviewQueue } from "@/components/review-queue";
import { TenderDetail } from "@/components/tender-detail";
import { TenderFeed } from "@/components/tender-feed";
import { Card, CardContent } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  movePipelineEntry,
  trackTender,
  untrackTender,
  type ActionResult,
} from "@/lib/actions/pipeline";
import type { WorkspaceCounts } from "@/lib/api";
import { isSupabaseConfigured } from "@/lib/supabase/config";
import type { PipelineEntry, PipelineStatus, Tender, TenderMatch } from "@/lib/types";

export function Workspace({
  tenders,
  matches,
  pipeline: initialPipeline,
  counts,
}: {
  tenders: Tender[];
  matches: TenderMatch[];
  pipeline: PipelineEntry[];
  /** Counted against the whole corpus. The feed is a window of a few hundred
   *  rows, so deriving these from `tenders` describes the page rather than
   *  the market -- which is how this screen came to report 488 open tenders
   *  on a database holding 4,786. */
  counts: WorkspaceCounts;
}) {
  const [selected, setSelected] = useState<Tender | null>(null);
  const [pipeline, setPipeline] = useState<PipelineEntry[]>(initialPipeline);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  /**
   * Apply a pipeline change optimistically, then let the database confirm it.
   *
   * The screen updates first because the alternative — a spinner on every
   * card drag — makes a board feel broken. But the row is the organization's
   * record of what it is bidding on, so an optimistic update that quietly
   * survived a refused write would be worse than no update at all: the board
   * would show a bid nobody is running. On failure the previous list is put
   * back and the reason is shown.
   *
   * One write at a time, enforced by `busy`. It keeps `previous` below from
   * going stale under a second overlapping change, which would roll back to a
   * list that never existed.
   */
  async function apply(next: PipelineEntry[], run: () => Promise<ActionResult>) {
    if (busy) return;
    const previous = pipeline;

    setPipeline(next);
    setError(null);

    // Fixtures have no database behind them. The board is a demonstration
    // there, and moving a card is allowed to be nothing more than that —
    // calling the action would only produce an error about a project that was
    // never configured.
    if (!isSupabaseConfigured) return;

    setBusy(true);
    try {
      const result = await run();
      if (!result.ok) {
        setPipeline(previous);
        setError(result.error);
      }
    } catch (cause) {
      // A network failure, or a server action that threw. Same outcome as a
      // refusal: put the board back and say so, rather than leave someone
      // looking at a move that never landed.
      setPipeline(previous);
      setError(cause instanceof Error ? cause.message : "The change could not be saved.");
    } finally {
      setBusy(false);
    }
  }

  function handleMove(tenderId: string, status: PipelineStatus) {
    apply(
      pipeline.map((entry) => (entry.tender_id === tenderId ? { ...entry, status } : entry)),
      () => movePipelineEntry(tenderId, status),
    );
  }

  function handleTrack(tenderId: string) {
    if (pipeline.some((entry) => entry.tender_id === tenderId)) return;
    apply([...pipeline, { tender_id: tenderId, status: "new", owner: null, notes: null }], () =>
      trackTender(tenderId),
    );
  }

  function handleUntrack(tenderId: string) {
    apply(
      pipeline.filter((entry) => entry.tender_id !== tenderId),
      () => untrackTender(tenderId),
    );
  }

  const inFlight = pipeline.filter((entry) =>
    ["evaluating", "drafting", "submitted"].includes(entry.status),
  ).length;
  // Matches the review queue's own filter exactly: a figure that disagrees
  // with the list it links to is worse than no figure. Safe to count from the
  // loaded set because `fetchWorkspace` loads every such tender by id rather
  // than leaving it to the feed window.
  const needsReview = tenders.filter(
    (t) => t.extraction_status === "needs_review" || t.extraction_status === "failed",
  ).length;

  const selectedEntry = selected
    ? (pipeline.find((entry) => entry.tender_id === selected.id) ?? null)
    : null;

  return (
    <div className="space-y-6">
      {error && (
        <div
          role="alert"
          className="flex items-start justify-between gap-4 rounded-md border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-900"
        >
          <p>
            <span className="font-medium">The pipeline change was not saved.</span> {error}
          </p>
          <button
            type="button"
            onClick={() => setError(null)}
            className="shrink-0 text-xs text-rose-700 underline"
          >
            Dismiss
          </button>
        </div>
      )}

      <section className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Open tenders" value={counts.open} />
        <Stat
          label="Closing within 7 days"
          value={counts.closingWithinSevenDays}
          tone={counts.closingWithinSevenDays > 0 ? "urgent" : "default"}
        />
        <Stat label="Bids in flight" value={inFlight} />
        <Stat
          label="Awaiting human review"
          value={needsReview}
          tone={needsReview > 0 ? "warning" : "default"}
        />
      </section>

      <div className="grid gap-6 lg:grid-cols-[1fr_20rem]">
        {/* min-w-0 is load-bearing: without it the pipeline board's min-w-max
            widens this grid track and pushes the alerts panel off-screen. */}
        <Tabs defaultValue="feed" className="min-w-0">
          <TabsList>
            <TabsTrigger value="feed">Tender feed</TabsTrigger>
            <TabsTrigger value="pipeline">Pipeline</TabsTrigger>
            <TabsTrigger value="review">
              Review{needsReview > 0 ? ` (${needsReview})` : ""}
            </TabsTrigger>
          </TabsList>

          <TabsContent value="feed">
            <TenderFeed tenders={tenders} matches={matches} onSelect={setSelected} />
          </TabsContent>

          <TabsContent value="pipeline">
            {/* An empty board is the normal first state, and an empty board
                with no explanation reads as a broken one. Say where cards
                come from. */}
            {pipeline.length === 0 && (
              <p className="mb-3 rounded-md border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-600">
                Nothing is being tracked yet. Open a tender and choose{" "}
                <span className="font-medium">Track this bid</span> to put it on the board.
              </p>
            )}
            <PipelineBoard
              tenders={tenders}
              entries={pipeline}
              onSelect={setSelected}
              onMove={handleMove}
              busy={busy}
            />
          </TabsContent>

          <TabsContent value="review">
            <ReviewQueue tenders={tenders} onSelect={setSelected} />
          </TabsContent>
        </Tabs>

        <aside>
          <AlertsPanel tenders={tenders} matches={matches} onSelect={setSelected} />
        </aside>
      </div>

      {selected && (
        <TenderDetail
          tender={selected}
          onClose={() => setSelected(null)}
          // Fixtures are not writable: there is no database behind them, and
          // a control that always fails is worse than no control.
          pipeline={
            isSupabaseConfigured
              ? {
                  status: selectedEntry?.status ?? null,
                  busy,
                  onTrack: () => handleTrack(selected.id),
                  onMove: (status) => handleMove(selected.id, status),
                  onUntrack: () => handleUntrack(selected.id),
                }
              : undefined
          }
        />
      )}
    </div>
  );
}

function Stat({
  label,
  value,
  tone = "default",
}: {
  label: string;
  value: number;
  tone?: "default" | "urgent" | "warning";
}) {
  const valueColor =
    tone === "urgent" ? "text-rose-600" : tone === "warning" ? "text-amber-600" : "text-slate-900";

  return (
    <Card>
      <CardContent className="p-4">
        <p className="text-xs uppercase tracking-wide text-slate-500">{label}</p>
        <p className={`mt-1 text-2xl font-semibold tabular-nums ${valueColor}`}>{value}</p>
      </CardContent>
    </Card>
  );
}
