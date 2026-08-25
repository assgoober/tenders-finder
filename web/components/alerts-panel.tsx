"use client";

/**
 * Alert feed.
 *
 * Each alert shows *why* it fired — the profile that matched, the similarity
 * score, and the keywords that hit. A score with no explanation is something
 * a BD team learns to ignore after the first bad match; showing the reason is
 * what makes the engine correctable rather than merely noisy.
 */

import { Bell } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { Tender, TenderMatch } from "@/lib/types";
import { deadlineLabel } from "@/lib/utils";

export function AlertsPanel({
  tenders,
  matches,
  onSelect,
}: {
  tenders: Tender[];
  matches: TenderMatch[];
  onSelect: (tender: Tender) => void;
}) {
  const tenderById = new Map(tenders.map((tender) => [tender.id, tender]));
  const ranked = [...matches].sort((a, b) => b.score - a.score);

  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between">
        <CardTitle className="flex items-center gap-2">
          <Bell className="h-4 w-4 text-slate-500" />
          Alerts
        </CardTitle>
        <Badge variant="secondary">{ranked.length}</Badge>
      </CardHeader>
      <CardContent className="space-y-2">
        {ranked.map((match) => {
          const tender = tenderById.get(match.tender_id);
          if (!tender) return null;

          return (
            <button
              key={`${match.profile_name}-${match.tender_id}`}
              type="button"
              onClick={() => onSelect(tender)}
              className="w-full rounded-md border border-slate-200 p-3 text-left transition-colors hover:bg-slate-50"
            >
              <div className="flex items-start justify-between gap-2">
                <p className="line-clamp-2 text-sm font-medium text-slate-900">{tender.title}</p>
                <Badge variant={match.score >= 0.85 ? "success" : "info"}>
                  {Math.round(match.score * 100)}%
                </Badge>
              </div>

              <p className="mt-1 text-xs text-slate-500">
                matched <span className="font-medium text-slate-600">{match.profile_name}</span> ·{" "}
                {deadlineLabel(tender.deadline)}
              </p>

              {match.matched_keywords.length > 0 && (
                <ul className="mt-2 flex flex-wrap gap-1">
                  {match.matched_keywords.map((keyword) => (
                    <li key={keyword}>
                      <Badge variant="outline" className="font-mono text-[10px]">
                        {keyword}
                      </Badge>
                    </li>
                  ))}
                </ul>
              )}
            </button>
          );
        })}

        {ranked.length === 0 && (
          <p className="py-6 text-center text-sm text-slate-500">
            No matches yet. Alerts appear here when a new tender scores above your profile
            threshold.
          </p>
        )}
      </CardContent>
    </Card>
  );
}
