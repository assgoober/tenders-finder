import { describe, expect, it, vi, beforeEach } from "vitest";
import { NextRequest } from "next/server";

const exchangeCodeForSession = vi.fn();
const verifyOtp = vi.fn();
const signOut = vi.fn();

vi.mock("@/lib/supabase/config", () => ({ isSupabaseConfigured: true }));
vi.mock("@/lib/supabase/server", () => ({
  createClient: async () => ({ auth: { exchangeCodeForSession, verifyOtp, signOut } }),
}));

import { GET } from "@/app/auth/callback/route";
import { POST } from "@/app/auth/signout/route";
import { redirectToPath } from "@/lib/http/relative-redirect";

// Railway forwards to the container, so the request the app sees carries the
// container's address -- the origin that leaked into Location on 2026-10-15.
const BEHIND_PROXY = "https://localhost:8080/auth/callback";

beforeEach(() => {
  exchangeCodeForSession.mockReset().mockResolvedValue({ error: null });
  verifyOtp.mockReset().mockResolvedValue({ error: null });
});

describe("auth redirects behind a proxy", () => {
  it("never puts the container address in Location", async () => {
    const res = await GET(new NextRequest(`${BEHIND_PROXY}?error=access_denied`));
    expect(res.status).toBe(307);
    expect(res.headers.get("location")).toBe("/login?error=link");
  });

  it("sends a confirmed link to next as a path", async () => {
    const res = await GET(new NextRequest(`${BEHIND_PROXY}?code=abc&next=/onboarding`));
    expect(exchangeCodeForSession).toHaveBeenCalledWith("abc");
    expect(res.headers.get("location")).toBe("/onboarding");
  });

  it("keeps the open-redirect guard", async () => {
    const res = await GET(new NextRequest(`${BEHIND_PROXY}?code=abc&next=//evil.example`));
    expect(res.headers.get("location")).toBe("/");
  });

  it("token_hash links work and a failed exchange goes to the error page", async () => {
    const ok = await GET(new NextRequest(`${BEHIND_PROXY}?token_hash=t&type=recovery&next=/reset-password`));
    expect(ok.headers.get("location")).toBe("/reset-password");
    exchangeCodeForSession.mockResolvedValue({ error: new Error("expired") });
    const bad = await GET(new NextRequest(`${BEHIND_PROXY}?code=old`));
    expect(bad.headers.get("location")).toBe("/login?error=link");
  });

  it("sign-out answers 303 to /login", async () => {
    const res = await POST();
    expect(res.status).toBe(303);
    expect(res.headers.get("location")).toBe("/login");
  });

  it("refuses anything that is not a same-site path", () => {
    expect(() => redirectToPath("https://evil.example")).toThrow();
    expect(() => redirectToPath("//evil.example")).toThrow();
  });
});
