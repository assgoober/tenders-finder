/**
 * Says, in the interface itself, that these numbers are invented.
 *
 * The workspace falls back to `lib/mock-data.ts` when no Supabase project is
 * configured, which keeps a fresh checkout and CI working. The risk that
 * creates is specific: the fixtures are realistic enough to be acted on, and a
 * deadline that is not real is exactly the kind of thing someone diaries. So
 * the fallback is stated where the data is read, not only in a README.
 */
export function DemoNotice() {
  return (
    <div
      role="status"
      className="mb-6 rounded-md border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900"
    >
      <span className="font-semibold">Demo data.</span> No Supabase project is configured for this
      build, so the workspace is rendering fixtures from <code>lib/mock-data.ts</code>. Every tender,
      deadline and match below is invented. Set <code>NEXT_PUBLIC_SUPABASE_URL</code> and{" "}
      <code>NEXT_PUBLIC_SUPABASE_ANON_KEY</code> to read live data.
    </div>
  );
}
