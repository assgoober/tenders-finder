/**
 * Rewrite the sharing card's og:image to an absolute URL.
 *
 *   node tools/stamp-origin.mjs https://tenders-finder.vercel.app
 *
 * Link previews are fetched by crawlers that do not run JavaScript, so a
 * relative og:image cannot be resolved at runtime — it has to be absolute in
 * the served HTML. Slack and Facebook resolve relative ones against the page
 * URL anyway; X does not. The deployment host is not knowable until the
 * deployment exists, so this runs after the first deploy, against the real
 * domain, and then the page is redeployed.
 *
 * Idempotent: re-running it with a different origin replaces the previous
 * value rather than stacking.
 */
import { readFile, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const origin = process.argv[2];
if (!origin) {
  console.error("usage: node tools/stamp-origin.mjs https://your-domain");
  process.exit(1);
}

let base;
try {
  base = new URL(origin);
} catch {
  console.error(`"${origin}" is not a URL. Include the scheme: https://your-domain`);
  process.exit(1);
}
if (base.protocol !== "https:") {
  // Crawlers routinely refuse to fetch a card image over plain http.
  console.error("The origin must be https — crawlers skip http card images.");
  process.exit(1);
}

const file = resolve(dirname(fileURLToPath(import.meta.url)), "..", "index.html");
const html = await readFile(file, "utf8");

const tag = /(<meta property="og:image" content=")([^"]*)(">)/;
if (!tag.test(html)) {
  console.error("No og:image tag found in index.html — nothing to stamp.");
  process.exit(1);
}

const absolute = new URL("og.png", base).href;
const next = html.replace(tag, `$1${absolute}$3`);

if (next === html) {
  console.log(`og:image already ${absolute} — nothing to do.`);
} else {
  await writeFile(file, next);
  console.log(`og:image -> ${absolute}`);
  console.log("Redeploy for crawlers to see it, then re-share the link.");
}
