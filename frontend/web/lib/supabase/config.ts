/**
 * Whether this build has a Supabase project to talk to.
 *
 * The dashboard was built against `lib/mock-data.ts` so it could be worked on
 * before a database existed, and that is still the state of a fresh checkout,
 * of CI, and of anyone running `npm run dev` without credentials. Rather than
 * make those cases crash on a missing URL, every entry point asks this first
 * and falls back to the fixtures.
 *
 * The fallback is deliberately loud in the UI (see `DemoNotice`) — a
 * dashboard that silently shows invented tenders is worse than one that
 * refuses to start, because someone will act on the numbers.
 */

export const SUPABASE_URL = process.env.NEXT_PUBLIC_SUPABASE_URL ?? "";
export const SUPABASE_ANON_KEY = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ?? "";

export const isSupabaseConfigured = SUPABASE_URL !== "" && SUPABASE_ANON_KEY !== "";
