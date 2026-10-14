/**
 * Browser-side reading of a failed turn, and of the message box.
 * Imports only plain constants, so it is safe in a client component.
 */
import { USER_MESSAGES } from "./errors";
import { MAX_MESSAGE_CHARS } from "./schema";

export type ApiError = { code: string; message: string };

export const MESSAGE_WARN_AT = 3500;

/**
 * A server error with its own `{error}` body is shown as sent. A 5xx without
 * one (the platform killed the function: gateway page, empty body) is almost
 * always a timeout, and the conversation is kept either way.
 */
export function apiErrorFrom(status: number, body: unknown): ApiError {
  const e = (body as { error?: { code?: unknown; message?: unknown } } | null)?.error;
  if (e && typeof e.code === "string" && typeof e.message === "string") {
    return { code: e.code, message: e.message };
  }
  if (status >= 500) return { code: "timeout", message: USER_MESSAGES.timeout };
  return { code: "upstream", message: USER_MESSAGES.upstream };
}

export function messageState(text: string): { length: number; warn: boolean; tooLong: boolean } {
  return {
    length: text.length,
    warn: text.length > MESSAGE_WARN_AT,
    tooLong: text.length > MAX_MESSAGE_CHARS,
  };
}
