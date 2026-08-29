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
 */

import { useState } from "react";

import { AlertsPanel } from "@/components/alerts-panel";
import { PipelineBoard } from "@/components/pipeline-board";
import { ReviewQueue } from "@/components/review-queue";
import { TenderDetail } from "@/components/tender-detail";
import { TenderFeed } from "@/components/tender-feed";
import { Card, CardContent } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import type { PipelineEntry, Tender, TenderMatch } from "@/lib/types";
import { daysUntil } from "@/lib/utils";

export function Workspace({
  tenders,
  matches,
  pipeline,
}: {
  tenders: Tender[];
  matches: TenderMatch[];
  pipeline: PipelineEntry[];
}) {
  const [selected, setSelected] = useState<Tender | null>(null);

  const openCount = tenders.filter((t) => t.status === "open" || t.status === "closing_soon").length;
  const closingThisWeek = tenders.filter((t) => {
    const days = daysUntil(t.deadline);
    return days !== null && days >= 0 && days <= 7;
  }).length;
  const inFlight = pipeline.filter((entry) =>
    ["evaluating", "drafting", "submitted"].includes(entry.status),
  ).length;
  // Matches the review queue's own filter exactly: a figure that disagrees
  // with the list it links to is worse than no figure.
  const needsReview = tenders.filter(
    (t) => t.extraction_status === "needs_review" || t.extraction_status === "failed",
  ).length;

  return (
    <div className="space-y-6">
      <section className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Open tenders" value={openCount} />
        <Stat label="Closing within 7 days" value={closingThisWeek} tone={closingThisWeek > 0 ? "urgent" : "default"} />
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
            <PipelineBoard tenders={tenders} entries={pipeline} onSelect={setSelected} />
          </TabsContent>

          <TabsContent value="review">
            <ReviewQueue tenders={tenders} onSelect={setSelected} />
          </TabsContent>
        </Tabs>

        <aside>
          <AlertsPanel tenders={tenders} matches={matches} onSelect={setSelected} />
        </aside>
      </div>

      {selected && <TenderDetail tender={selected} onClose={() => setSelected(null)} />}
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
