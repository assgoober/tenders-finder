import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/**
 * Days until a deadline. Negative once it has passed.
 *
 * Computed against midnight boundaries rather than exact timestamps: a
 * deadline at 09:00 tomorrow is "1 day", not "0 days", which is how a bid
 * manager counts.
 */
export function daysUntil(iso: string | null): number | null {
  if (!iso) return null;
  const target = new Date(iso);
  if (Number.isNaN(target.getTime())) return null;

  const startOfTarget = new Date(target.getFullYear(), target.getMonth(), target.getDate());
  const now = new Date();
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate());

  return Math.round((startOfTarget.getTime() - startOfToday.getTime()) / 86_400_000);
}

export function formatDate(iso: string | null): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

export function formatCurrency(value: number | null, currency: string | null): string {
  if (value === null) return "—";
  try {
    return new Intl.NumberFormat(undefined, {
      style: currency ? "currency" : "decimal",
      currency: currency ?? undefined,
      maximumFractionDigits: 0,
    }).format(value);
  } catch {
    // An unrecognized currency code must not blank out the value.
    return `${value.toLocaleString()} ${currency ?? ""}`.trim();
  }
}

/** Human-readable deadline pressure — the single most scanned field in the feed. */
export function deadlineLabel(iso: string | null): string {
  const days = daysUntil(iso);
  if (days === null) return "No deadline";
  if (days < 0) return `Closed ${Math.abs(days)}d ago`;
  if (days === 0) return "Closes today";
  if (days === 1) return "Closes tomorrow";
  return `${days} days left`;
}
