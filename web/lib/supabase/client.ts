"use client";

import { createBrowserClient } from "@supabase/ssr";

import { SUPABASE_ANON_KEY, SUPABASE_URL } from "@/lib/supabase/config";

/**
 * Supabase client for the browser. Anon key only — it is compiled into the
 * bundle and every visitor can read it, which is safe precisely because RLS
 * applies to it. The service-role key must never reach this file.
 */
export function createClient() {
  return createBrowserClient(SUPABASE_URL, SUPABASE_ANON_KEY);
}
