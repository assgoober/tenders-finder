/**
 * Write the coverage numbers on the landing page straight out of the database.
 *
 * This exists because of a small, entirely predictable failure. The page was
 * asked to be updated to "696 tenders, 29 countries" -- and by the time the
 * edit was made the database held 792 across 31. Nobody was careless: a number
 * typed into a static page is a snapshot, and a snapshot of a system that
 * ingests nightly is stale before it ships.
 *
 * So the numbers are no longer typed. Each one lives in exactly one element,
 * tagged `data-stat`, and this script rewrites those elements in place. There
 * is no second copy that can fall behind, and `npm run stats -- --check` fails
 * CI when the page and the database have drifted -- which turns a silent
 * marketing lie into a red build.
 *
 * Israel is counted from `tender_sources.country` rather than from the
 * listing metadata: BALAM rows carry no buyer country because the portal is
 * single-country and never states one. Dropping them would undercount by a
 * whole market.
 */
import { readFileSync, writeFileSync } from "node:fs";
import { createClient } from "@supabase/supabase-js";

const PAGE = new URL("../index.html", import.meta.url);
const CHECK = process.argv.includes("--check");

const url = process.env.SUPABASE_URL;
const key = process.env.SUPABASE_SERVICE_ROLE_KEY;
if (!url || !key) {
  console.error("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required.");
  process.exit(2);
}

const db = createClient(url, key, { auth: { persistSession: false } });

/** Count rows without pulling them. `head: true` sends no body. */
async function count(table, apply = (q) => q) {
  const { count: n, error } = await apply(
    db.from(table).select("*", { count: "exact", head: true })
  );
  if (error) throw new Error(`${table}: ${error.message}`);
  return n ?? 0;
}

const tenders = await count("tenders");
const live = await count("tenders", (q) => q.in("status", ["open", "closing_soon"]));
const sources = await count("tender_sources");

// Distinct countries needs the rows, but only two columns of them.
const { data: rows, error } = await db
  .from("tenders")
  .select("metadata->listing->>buyer_country, tender_sources(country)")
  .limit(20000);
if (error) throw new Error(`countries: ${error.message}`);

const seen = new Set();
for (const r of rows ?? []) {
  const c = r.buyer_country || r.tender_sources?.country;
  if (c) seen.add(String(c).toUpperCase());
}
const countries = seen.size;

const today = new Date();
const iso = today.toISOString().slice(0, 10);
const he = `${String(today.getDate()).padStart(2, "0")}.${String(
  today.getMonth() + 1
).padStart(2, "0")}.${today.getFullYear()}`;

const wanted = { tenders, live, countries, sources, asof: he };

let html = readFileSync(PAGE, "utf8");
const drift = [];

for (const [stat, value] of Object.entries(wanted)) {
  const re = new RegExp(`(data-stat="${stat}"[^>]*>)([^<]*)(<)`);
  const m = html.match(re);
  if (!m) {
    console.error(`no element carries data-stat="${stat}"`);
    process.exit(2);
  }
  if (m[2].trim() !== String(value)) {
    drift.push(`${stat}: page says ${m[2].trim()}, database says ${value}`);
  }
  html = html.replace(re, `$1${value}$3`);
}
html = html.replace(/(datetime=")[^"]*("[^>]*data-stat="asof")/, `$1${iso}$2`);

if (CHECK) {
  if (drift.length) {
    console.error("landing page is behind the database:");
    for (const d of drift) console.error(`  ${d}`);
    console.error("\nfix: npm run stats");
    process.exit(1);
  }
  console.log(`ok  page matches the database (${tenders} tenders, ${countries} countries)`);
  process.exit(0);
}

writeFileSync(PAGE, html);
console.log(`tenders   ${tenders}`);
console.log(`live      ${live}`);
console.log(`countries ${countries}`);
console.log(`sources   ${sources}`);
console.log(`as of     ${he}`);
console.log(drift.length ? `updated ${drift.length} stale number(s)` : "already current");
