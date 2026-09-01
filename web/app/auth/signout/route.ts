import { NextResponse } from "next/server";

import { createClient } from "@/lib/supabase/server";

/**
 * Sign out. POST only — a GET would let any image or link on a page log the
 * user out by being loaded.
 */
export async function POST(request: Request) {
  const supabase = await createClient();
  await supabase.auth.signOut();
  return NextResponse.redirect(new URL("/login", request.url), { status: 303 });
}
