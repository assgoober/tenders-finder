/** Who is sent to the onboarding chat after login. */
export const SKIP_COOKIE = "tf_onboarding_skipped";

export function shouldOnboard(s: { hasOrganization: boolean; hasProfile: boolean; skipped: boolean }): boolean {
  return s.hasOrganization && !s.hasProfile && !s.skipped;
}
