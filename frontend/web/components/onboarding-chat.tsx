"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { saveProfile } from "@/lib/actions/profiles";
import { apiErrorFrom, messageState, type ApiError } from "@/lib/onboarding/client-errors";
import { USER_MESSAGES } from "@/lib/onboarding/errors";
import { descriptionProblem, isStaleResponse, needsPreviewRefresh, type PreviewInput } from "@/lib/onboarding/preview-state";
import { SKIP_COOKIE } from "@/lib/onboarding/gate";
import { saveDrafts } from "@/lib/onboarding/save";
import type { Candidate, DraftView, Preview, Research } from "@/lib/onboarding/schema";

type Msg = { role: "user" | "assistant"; text: string };
type DraftCard = DraftView & { checked: boolean; previewInput: PreviewInput | null };

/** A preview counts as computed for this text only if it is a real answer. */
const previewInputOf = (d: DraftView): PreviewInput | null =>
  d.preview && d.preview.status !== "unavailable"
    ? { description: d.description, excludedKeywords: d.excludedKeywords }
    : null;

const INPUT =
  "w-full rounded-md border border-slate-300 px-3 py-2 text-sm outline-none focus:border-slate-900 focus:ring-1 focus:ring-slate-900";

const toView = (d: DraftCard): DraftView => ({
  key: d.key,
  name: d.name,
  description: d.description,
  keywords: d.keywords,
  excludedKeywords: d.excludedKeywords,
  sources: d.sources,
  unconfirmed: d.unconfirmed,
  preview: d.preview,
});

function PreviewBox({ preview }: { preview: Preview | null }) {
  if (!preview) return null;
  if (preview.status === "empty_corpus") {
    return <p className="text-xs text-slate-500">No open tenders in the database yet, so nothing to preview.</p>;
  }
  if (preview.status === "disabled") {
    return <p className="text-xs text-slate-500">Preview off: {preview.reason}.</p>;
  }
  if (preview.status === "unavailable") {
    return <p className="text-xs text-slate-500">Preview unavailable right now — try again later.</p>;
  }
  return (
    <div className="rounded-md bg-slate-50 px-3 py-2 text-xs text-slate-700">
      <p className="font-medium">
        Would match {preview.total} of {preview.openTenders} open tenders right now
      </p>
      {preview.top.length > 0 ? (
        <ul className="mt-1 list-disc pl-4">
          {preview.top.map((t) => (
            <li key={t.id} dir="auto">
              {t.title}
              {t.deadline ? ` — closes ${new Date(t.deadline).toLocaleDateString()}` : ""}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

export function OnboardingChat({ unavailable }: { unavailable: boolean }) {
  const router = useRouter();
  const [messages, setMessages] = useState<Msg[]>([]);
  const [drafts, setDrafts] = useState<DraftCard[]>([]);
  const [research, setResearch] = useState<Research | null>(null);
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState<ReadonlySet<string>>(new Set());
  const [saveErrors, setSaveErrors] = useState<Record<string, string>>({});
  const draftsRef = useRef<DraftCard[]>([]);
  draftsRef.current = drafts;
  const inFlight = useRef<Set<string>>(new Set());

  const anyInvalid = drafts.some((d) => descriptionProblem(d.description) !== null);
  const msg = messageState(input);
  const canSend = !busy && !saving && !anyInvalid && !msg.tooLong && input.trim().length > 0;

  const unsaved = drafts.some((d) => d.checked && !saved.has(d.key));
  useEffect(() => {
    if (!unsaved) return;
    const warn = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [unsaved]);

  function skip() {
    document.cookie = `${SKIP_COOKIE}=1; path=/; max-age=31536000; samesite=lax`;
    router.push("/");
    router.refresh();
  }

  async function send(text: string) {
    const trimmed = text.trim();
    if (!trimmed || busy || saving || anyInvalid || text.length > 4000) return;
    const previous = messages;
    const next: Msg[] = [...messages, { role: "user", text: trimmed }];
    setMessages(next);
    setInput("");
    setError(null);
    setBusy(true);
    try {
      const res = await fetch("/api/onboarding", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ messages: next, drafts: drafts.map(toView), research }),
      });
      let body: Record<string, unknown> | null = null;
      try {
        body = await res.json();
      } catch {
        body = null; // A gateway timeout page is not JSON.
      }
      if (!res.ok || body === null) {
        setError(apiErrorFrom(res.ok ? 504 : res.status, body));
        setMessages(previous);
        setInput(trimmed);
        return;
      }
      setMessages([...next, { role: "assistant", text: body.reply as string }]);
      setResearch(body.research as Research | null);
      setCandidates((body.candidates as Candidate[]) ?? []);
      setDrafts(
        (body.drafts as DraftView[]).map((d) => ({
          ...d,
          checked: drafts.find((p) => p.key === d.key)?.checked ?? !d.unconfirmed,
          previewInput: previewInputOf(d),
        })),
      );
    } catch {
      setError({ code: "network", message: "Could not reach the server. Check your connection and try again." });
      setMessages(previous);
      setInput(trimmed);
    } finally {
      setBusy(false);
    }
  }

  function patch(key: string, change: Partial<DraftCard>) {
    setDrafts((ds) => ds.map((d) => (d.key === key ? { ...d, ...change } : d)));
  }

  async function refreshPreview(d: DraftCard) {
    if (!needsPreviewRefresh(d)) return;
    const sent = { description: d.description, excludedKeywords: d.excludedKeywords };
    const flightKey = JSON.stringify([d.key, sent.description, sent.excludedKeywords]);
    if (inFlight.current.has(flightKey)) return;
    inFlight.current.add(flightKey);
    try {
      const res = await fetch("/api/onboarding/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(sent),
      });
      if (!res.ok) return;
      const preview = (await res.json()).preview as Preview;
      const current = draftsRef.current.find((x) => x.key === d.key);
      // The user kept typing, or a newer turn replaced the draft: drop it.
      if (isStaleResponse(current, sent.description, sent.excludedKeywords)) return;
      patch(d.key, { preview, previewInput: previewInputOf({ ...d, preview }) });
    } catch {
      // Keep the previous preview; a preview is a convenience, not a gate.
    } finally {
      inFlight.current.delete(flightKey);
    }
  }

  async function create() {
    if (saving || busy || anyInvalid) return;
    setSaving(true);
    setSaveErrors({});
    const chosen = drafts.filter((d) => d.checked).map(toView);
    const outcomes = await saveDrafts(chosen, saved, saveProfile);
    const nowSaved = new Set(saved);
    const errors: Record<string, string> = {};
    for (const o of outcomes) {
      if (o.ok) nowSaved.add(o.key);
      else errors[o.key] = o.error;
    }
    setSaved(nowSaved);
    setSaveErrors(errors);
    setSaving(false);
    if (Object.keys(errors).length === 0) {
      router.push("/?onboarded=1");
      router.refresh();
    }
  }

  if (unavailable) {
    return (
      <Card>
        <CardContent className="space-y-3 py-5 text-sm text-slate-700">
          <p>Chat setup isn&apos;t available here. You can describe your company in the form instead.</p>
          <div className="flex gap-2">
            <Link href="/profiles" className="underline">
              Set up with the form
            </Link>
            <button type="button" onClick={skip} className="text-slate-500 underline">
              Skip for now
            </button>
          </div>
        </CardContent>
      </Card>
    );
  }

  const checkedCount = drafts.filter((d) => d.checked && !saved.has(d.key)).length;

  return (
    <div className="space-y-4">
      <div className="space-y-3">
        {messages.map((m, i) => (
          <p
            key={i}
            dir="auto"
            className={
              m.role === "user"
                ? "ml-auto max-w-[85%] whitespace-pre-line rounded-md bg-slate-900 px-3 py-2 text-sm text-white"
                : "max-w-[85%] whitespace-pre-line rounded-md bg-slate-100 px-3 py-2 text-sm text-slate-800"
            }
          >
            {m.text}
          </p>
        ))}
        {busy ? <p className="text-sm text-slate-500">Looking into it…</p> : null}
      </div>

      {candidates.length > 0 ? (
        <div className="flex flex-wrap gap-2">
          {candidates.map((c) => (
            <Button
              key={`${c.name}-${c.website ?? ""}`}
              type="button"
              variant="outline"
              disabled={busy || saving || anyInvalid}
              onClick={() => send(`It's ${c.name}${c.website ? ` (${c.website})` : ""}${c.country ? `, ${c.country}` : ""}`)}
            >
              {c.name}
              {c.country ? ` · ${c.country}` : ""}
            </Button>
          ))}
        </div>
      ) : null}

      {drafts.map((d) => (
        <Card key={d.key}>
          <CardContent className="space-y-2 py-4">
            <label className="flex items-center gap-2 text-sm font-semibold text-slate-900">
              <input
                type="checkbox"
                checked={d.checked}
                disabled={saved.has(d.key)}
                onChange={(e) => patch(d.key, { checked: e.target.checked })}
              />
              <span dir="auto">{d.name}</span>
              {saved.has(d.key) ? <span className="text-xs font-normal text-green-700">Saved</span> : null}
            </label>
            {d.unconfirmed ? (
              <p className="text-xs text-amber-700">Unconfirmed — no source backs this. Check it before saving.</p>
            ) : null}
            <textarea
              dir="auto"
              rows={4}
              value={d.description}
              disabled={saved.has(d.key)}
              readOnly={busy}
              onChange={(e) => patch(d.key, { description: e.target.value })}
              onBlur={() => refreshPreview(d)}
              className={INPUT}
            />
            {descriptionProblem(d.description) ? (
              <p role="alert" className="text-xs text-red-600">
                {descriptionProblem(d.description)}
              </p>
            ) : null}
            {d.keywords.length > 0 ? <p className="text-xs text-slate-500">Keywords: {d.keywords.join(", ")}</p> : null}
            {d.excludedKeywords.length > 0 ? (
              <p className="text-xs text-slate-500">Never: {d.excludedKeywords.join(", ")}</p>
            ) : null}
            {d.sources.length > 0 ? (
              <p className="text-xs text-slate-500">
                Sources:{" "}
                {d.sources.map((s, i) => (
                  <span key={s.url}>
                    {i > 0 ? ", " : ""}
                    <a href={s.url} target="_blank" rel="noreferrer noopener" className="underline">
                      {s.title}
                    </a>
                  </span>
                ))}
              </p>
            ) : null}
            <PreviewBox preview={d.preview} />
            {saveErrors[d.key] ? (
              <p role="alert" className="text-sm text-red-600">
                {saveErrors[d.key]}
              </p>
            ) : null}
          </CardContent>
        </Card>
      ))}

      {error ? (
        <p role="alert" className="text-sm text-red-600">
          {error.message}{" "}
          {error.code === "signed_out" ? (
            <Link href="/login" className="underline">
              Sign in
            </Link>
          ) : null}
          {error.code === "unavailable" || error.code === "quota" ? (
            <Link href="/profiles" className="underline">
              Use the form
            </Link>
          ) : null}
        </p>
      ) : null}

      <form
        onSubmit={(e) => {
          e.preventDefault();
          send(input);
        }}
        className="space-y-2"
      >
        <textarea
          dir="auto"
          rows={3}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              if (canSend) send(input);
            }
          }}
          placeholder={messages.length === 0 ? "e.g. I work at … — or: tactical radios and night vision for armies" : "Say what to change…"}
          className={INPUT}
        />
        {msg.warn ? (
          <p className="text-xs text-slate-500">
            {msg.length.toLocaleString("en-US")} / 4,000 characters
          </p>
        ) : null}
        {msg.tooLong ? (
          <p role="alert" className="text-sm text-red-600">
            {USER_MESSAGES.too_long}
          </p>
        ) : null}
        {anyInvalid ? (
          <p className="text-xs text-red-600">
            Fix the profile descriptions marked above before sending or creating.
          </p>
        ) : null}
        <div className="flex flex-wrap items-center gap-2">
          <Button type="submit" disabled={!canSend}>
            Send
          </Button>
          {checkedCount > 0 ? (
            <Button type="button" variant="outline" disabled={saving || busy || anyInvalid} onClick={create}>
              {saving ? "Saving…" : `Create ${checkedCount} ${checkedCount === 1 ? "profile" : "profiles"}`}
            </Button>
          ) : null}
          <Link href="/profiles" className="text-sm text-slate-500 underline">
            Set up with the form instead
          </Link>
          <button type="button" onClick={skip} className="text-sm text-slate-500 underline">
            Skip for now
          </button>
        </div>
      </form>
    </div>
  );
}
