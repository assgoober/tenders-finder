/**
 * Every way a turn can fail, with the sentence the user sees and the HTTP
 * status the route returns. One table, so the route and the UI cannot drift
 * apart on wording.
 */
export type AgentErrorCode =
  | "unavailable"
  | "quota"
  | "timeout"
  | "invalid_output"
  | "upstream"
  | "busy"
  | "cap"
  | "too_long"
  | "blocked"
  | "blocked_draft"
  | "signed_out"
  | "no_organization"
  | "bad_request";

export class AgentError extends Error {
  constructor(public readonly code: AgentErrorCode, detail?: string) {
    super(detail ?? code);
    this.name = "AgentError";
  }
}

export const USER_MESSAGES: Record<AgentErrorCode, string> = {
  unavailable:
    "Chat setup isn't available right now. You can set up your profile with the form instead.",
  quota:
    "The AI service has reached its limit for now. Try again later, or set up your profile with the form.",
  timeout: "That took too long. Try again — your conversation is kept.",
  invalid_output: "I couldn't put that together. Try rephrasing.",
  upstream: "Something went wrong reaching the AI service. Try again.",
  busy: "The AI service is overloaded right now. Try again in a minute — your conversation is kept.",
  cap: "This conversation has reached its length limit. Create the profiles you have, or start over.",
  too_long: "That message is too long — keep it under 4,000 characters.",
  blocked: "I can't look that up. Describe what your company makes instead.",
  blocked_draft: "I can't draft that. Try describing what your company makes in different words.",
  signed_out: "You've been signed out. Sign in again — this conversation can't be kept.",
  no_organization: "Your account isn't a member of an organization yet.",
  bad_request: "Something about that request didn't look right. Try again; if it keeps happening, start over.",
};

export const HTTP_STATUS: Record<AgentErrorCode, number> = {
  unavailable: 503,
  quota: 429,
  timeout: 504,
  invalid_output: 502,
  upstream: 502,
  busy: 503,
  cap: 429,
  too_long: 413,
  blocked: 422,
  blocked_draft: 422,
  signed_out: 401,
  no_organization: 403,
  bad_request: 400,
};

export function toAgentError(e: unknown): AgentError {
  if (e instanceof AgentError) return e;
  const err = (e ?? {}) as { status?: number; name?: string; message?: string };
  const status = typeof err.status === "number" ? err.status : undefined;
  const message = String(err.message ?? e);
  if (status === 429 || /RESOURCE_EXHAUSTED|exceeded your current quota/i.test(message)) return new AgentError("quota", message);
  if (err.name === "AbortError" || /timed? ?out|ETIMEDOUT|DEADLINE_EXCEEDED/i.test(message)) {
    return new AgentError("timeout", message);
  }
  if (isOverloaded(e)) return new AgentError("busy", message);
  if (status === 401 || status === 403 || /API key/i.test(message)) {
    return new AgentError("unavailable", message);
  }
  return new AgentError("upstream", message);
}

/**
 * Google answers 503 UNAVAILABLE "This model is currently experiencing high
 * demand" in bursts. Seen on the first live deploy (2026-10-15): about one
 * call in six failed for minutes at a time, on more than one model. It is
 * temporary, so it is retried (gemini.ts) and, if it outlasts the retries,
 * reported as "busy" rather than as a generic failure.
 */
export function isOverloaded(e: unknown): boolean {
  if (e instanceof AgentError) return e.code === "busy";
  const err = (e ?? {}) as { status?: number; message?: string };
  if (err.status === 503) return true;
  return /503|"UNAVAILABLE"|high demand|overloaded/i.test(String(err.message ?? e));
}
