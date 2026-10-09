import { describe, expect, it } from "vitest";

import { apiErrorFrom, MESSAGE_WARN_AT, messageState } from "@/lib/onboarding/client-errors";
import { USER_MESSAGES } from "@/lib/onboarding/errors";

describe("apiErrorFrom", () => {
  it("uses the server's own error when it sent one", () => {
    expect(apiErrorFrom(429, { error: { code: "quota", message: "Later." } })).toEqual({
      code: "quota",
      message: "Later.",
    });
  });
  it("treats a 504 with a non-JSON body as a timeout", () => {
    expect(apiErrorFrom(504, null)).toEqual({ code: "timeout", message: USER_MESSAGES.timeout });
  });
  it("treats any 5xx without a parseable error as a timeout", () => {
    expect(apiErrorFrom(502, { nope: 1 })).toEqual({ code: "timeout", message: USER_MESSAGES.timeout });
  });
  it("falls back to upstream for a 4xx without a parseable error", () => {
    expect(apiErrorFrom(404, null)).toEqual({ code: "upstream", message: USER_MESSAGES.upstream });
  });
});

describe("messageState", () => {
  it("is quiet for short text", () => {
    expect(messageState("hello")).toEqual({ length: 5, warn: false, tooLong: false });
  });
  it("warns near the limit and blocks past it", () => {
    expect(messageState("x".repeat(MESSAGE_WARN_AT + 1)).warn).toBe(true);
    expect(messageState("x".repeat(4000)).tooLong).toBe(false);
    expect(messageState("x".repeat(4001)).tooLong).toBe(true);
  });
});
