"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { saveProfile, setProfileActive } from "@/lib/actions/profiles";

export type ProfileView = {
  id: string;
  name: string;
  description: string;
  keywords: string[];
  excludedKeywords: string[];
  notifyEmail: boolean;
  isActive: boolean;
  /** False until the nightly sweep has embedded the current text. */
  embedded: boolean;
  matchCount: number;
};

const INPUT =
  "w-full rounded-md border border-slate-300 px-3 py-2 text-sm outline-none focus:border-slate-900 focus:ring-1 focus:ring-slate-900";

function Status({ profile }: { profile: ProfileView }) {
  if (!profile.isActive) return <span className="text-slate-500">Paused</span>;
  if (!profile.embedded) {
    // Said plainly, because the alternative reading of "no matches yet" is
    // "this is broken".
    return (
      <span className="text-amber-700">
        Waiting for tonight&apos;s run. Matches appear after it reads this text.
      </span>
    );
  }
  return (
    <span className="text-slate-600">
      Active · {profile.matchCount} {profile.matchCount === 1 ? "match" : "matches"} so far
    </span>
  );
}

function ProfileForm({
  profile,
  onDone,
}: {
  profile?: ProfileView;
  onDone: () => void;
}) {
  const router = useRouter();
  const [name, setName] = useState(profile?.name ?? "");
  const [description, setDescription] = useState(profile?.description ?? "");
  const [keywords, setKeywords] = useState(profile?.keywords.join(", ") ?? "");
  const [excluded, setExcluded] = useState(profile?.excludedKeywords.join(", ") ?? "");
  const [notifyEmail, setNotifyEmail] = useState(profile?.notifyEmail ?? true);
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  const textChanged = profile !== undefined && description.trim() !== profile.description.trim();
  const prefix = profile?.id ?? "new";

  function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    startTransition(async () => {
      const result = await saveProfile({
        id: profile?.id,
        name,
        description,
        keywords,
        excludedKeywords: excluded,
        notifyEmail,
      });
      if (!result.ok) {
        setError(result.error);
        return;
      }
      router.refresh();
      onDone();
    });
  }

  return (
    <form onSubmit={onSubmit} className="space-y-4">
      <div className="space-y-1.5">
        <label htmlFor={`${prefix}-name`} className="block text-sm font-medium text-slate-700">
          Name
        </label>
        <input
          id={`${prefix}-name`}
          required
          maxLength={120}
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g. Tactical radios"
          className={INPUT}
        />
      </div>

      <div className="space-y-1.5">
        <label htmlFor={`${prefix}-desc`} className="block text-sm font-medium text-slate-700">
          What you build and want to bid on
        </label>
        <textarea
          id={`${prefix}-desc`}
          required
          minLength={40}
          maxLength={4000}
          rows={6}
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          className={INPUT}
        />
        <p className="text-xs text-slate-500">
          A paragraph, in any language, the way you would describe your capabilities to a buyer.
          Tenders are matched on meaning, not exact words, so be specific about products,
          technologies and the customers you serve.
        </p>
        {textChanged ? (
          <p className="text-xs text-amber-700">
            Changing this text means matching starts again from tonight&apos;s run. Matches you
            already have are kept.
          </p>
        ) : null}
      </div>

      <div className="space-y-1.5">
        <label htmlFor={`${prefix}-kw`} className="block text-sm font-medium text-slate-700">
          Keywords <span className="font-normal text-slate-500">(optional)</span>
        </label>
        <input
          id={`${prefix}-kw`}
          value={keywords}
          onChange={(e) => setKeywords(e.target.value)}
          placeholder="SDR, MIL-STD-810, encryption"
          className={INPUT}
        />
        <p className="text-xs text-slate-500">
          Comma-separated. A tender that mentions one ranks slightly higher. Keywords do not
          bring in tenders the description would not match.
        </p>
      </div>

      <div className="space-y-1.5">
        <label htmlFor={`${prefix}-ex`} className="block text-sm font-medium text-slate-700">
          Never show tenders mentioning <span className="font-normal text-slate-500">(optional)</span>
        </label>
        <input
          id={`${prefix}-ex`}
          value={excluded}
          onChange={(e) => setExcluded(e.target.value)}
          placeholder="catering, cleaning services"
          className={INPUT}
        />
        <p className="text-xs text-slate-500">
          Comma-separated. A tender whose title or summary contains any of these is never matched.
        </p>
      </div>

      <label className="flex items-center gap-2 text-sm text-slate-700">
        <input
          type="checkbox"
          checked={notifyEmail}
          onChange={(e) => setNotifyEmail(e.target.checked)}
        />
        Email me when this profile matches a new tender
      </label>

      {error ? (
        <p role="alert" className="text-sm text-red-600">
          {error}
        </p>
      ) : null}

      <div className="flex gap-2">
        <Button type="submit" disabled={pending}>
          {pending ? "Saving…" : profile ? "Save changes" : "Create profile"}
        </Button>
        <Button type="button" variant="outline" disabled={pending} onClick={onDone}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

function ProfileCard({ profile }: { profile: ProfileView }) {
  const router = useRouter();
  const [editing, setEditing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  function toggleActive() {
    setError(null);
    startTransition(async () => {
      const result = await setProfileActive(profile.id, !profile.isActive);
      if (!result.ok) {
        setError(result.error);
        return;
      }
      router.refresh();
    });
  }

  return (
    <Card>
      <CardContent className="space-y-3 py-5">
        {editing ? (
          <ProfileForm profile={profile} onDone={() => setEditing(false)} />
        ) : (
          <>
            <div className="flex items-start justify-between gap-4">
              <div>
                <h2 className="text-base font-semibold text-slate-900">{profile.name}</h2>
                <p className="text-sm">
                  <Status profile={profile} />
                </p>
              </div>
              <div className="flex shrink-0 gap-2">
                <Button type="button" variant="outline" onClick={() => setEditing(true)}>
                  Edit
                </Button>
                <Button type="button" variant="outline" disabled={pending} onClick={toggleActive}>
                  {profile.isActive ? "Pause" : "Resume"}
                </Button>
              </div>
            </div>
            <p className="whitespace-pre-line text-sm text-slate-700">{profile.description}</p>
            {profile.keywords.length > 0 ? (
              <p className="text-xs text-slate-500">Keywords: {profile.keywords.join(", ")}</p>
            ) : null}
            {profile.excludedKeywords.length > 0 ? (
              <p className="text-xs text-slate-500">
                Never: {profile.excludedKeywords.join(", ")}
              </p>
            ) : null}
            {!profile.notifyEmail ? (
              <p className="text-xs text-slate-500">Email alerts off for this profile.</p>
            ) : null}
            {error ? (
              <p role="alert" className="text-sm text-red-600">
                {error}
              </p>
            ) : null}
          </>
        )}
      </CardContent>
    </Card>
  );
}

export function ProfileEditor({ profiles }: { profiles: ProfileView[] }) {
  const [creating, setCreating] = useState(false);
  // With no profiles there is nothing else on the page, so the form is the page.
  const showForm = creating || profiles.length === 0;

  return (
    <div className="space-y-4">
      {profiles.map((profile) => (
        <ProfileCard key={profile.id} profile={profile} />
      ))}

      {showForm ? (
        <Card>
          <CardContent className="py-5">
            <h2 className="mb-4 text-base font-semibold text-slate-900">
              {profiles.length === 0 ? "Create your first profile" : "New profile"}
            </h2>
            {/* Keyed on the count so a saved form remounts empty rather than
                showing the profile that was just created as a second draft. */}
            <ProfileForm key={profiles.length} onDone={() => setCreating(false)} />
          </CardContent>
        </Card>
      ) : (
        <Button type="button" onClick={() => setCreating(true)}>
          Add a profile
        </Button>
      )}
    </div>
  );
}
