/**
 * Generate en/index.html from index.html and content/en.json.
 *
 * `index.html` is the Hebrew source of truth and this script NEVER writes to
 * it. An earlier attempt annotated it in place with data-i18n keys; the
 * parser re-serialised the document and silently injected <html><head>
 * wrappers the page did not have. One-way generation removes that whole
 * class of risk: whatever the parser normalises affects only the output.
 *
 * The translation key is the Hebrew string itself. That is deliberate. A
 * numbered key shifts meaning when a paragraph is inserted above it, and the
 * English would attach to the wrong sentence with nothing looking broken.
 * Keyed on the text, an edited Hebrew string has no translation, and
 * `--check` fails. Copy that outruns its translation SHOULD fail the build.
 *
 *   node tools/build-en.mjs           # write en/index.html
 *   node tools/build-en.mjs --check   # fail if any string is untranslated
 */
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname } from "node:path";
import { fileURLToPath } from "node:url";
import * as cheerio from "cheerio";

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC = `${HERE}/../index.html`;
const DICT = `${HERE}/../content/en.json`;
const OUT = `${HERE}/../en/index.html`;
const CHECK = process.argv.includes("--check");

const HEBREW = /[֐-׿]/;
const SKIP = new Set(["script", "style", "code", "pre"]);
const ATTRS = ["alt", "title", "aria-label", "placeholder", "content"];

const dict = JSON.parse(readFileSync(DICT, "utf8"));
const $ = cheerio.load(readFileSync(SRC, "utf8"), { decodeEntities: false });

const missing = [];
const unused = new Set(Object.keys(dict));
let swapped = 0;

function translate(value) {
  const trimmed = value.trim();
  if (!HEBREW.test(trimmed)) return null;
  unused.delete(trimmed);
  if (!(trimmed in dict)) {
    missing.push(trimmed);
    return null;
  }
  swapped++;
  return value.replace(trimmed, dict[trimmed]);
}

$("*").each((_, el) => {
  const node = $(el);
  if (SKIP.has(el.tagName)) return;

  for (const attr of ATTRS) {
    const value = node.attr(attr);
    if (!value) continue;
    const next = translate(value);
    if (next !== null) node.attr(attr, next);
  }

  // Every DIRECT text child, not just leaf elements. An earlier version
  // translated only elements with no element children, and missed 21
  // strings -- all of them text sitting beside a child tag, like a heading
  // whose second half is wrapped in <em>. Those are the most prominent
  // strings on the page, which is a good argument for never trusting the
  // easy version of a rule.
  node.contents().each((_, child) => {
    if (child.type !== "text") return;
    const text = child.data;
    if (!text) return;
    const next = translate(text);
    if (next !== null) child.data = next;
  });
});

if (missing.length) {
  console.error(`${missing.length} string(s) have no English:\n`);
  for (const m of [...new Set(missing)].slice(0, 20)) console.error(`  ${m}`);
  console.error(`\nfix: add them to landing/content/en.json`);
  process.exit(1);
}

if (unused.size) {
  // Not fatal: a translation whose Hebrew was edited is stale, not broken.
  // Saying so keeps the file from silently growing a museum of old copy.
  console.warn(`${unused.size} translation(s) no longer match any Hebrew string:`);
  for (const u of [...unused].slice(0, 5)) console.warn(`  ${u.slice(0, 60)}`);
}

// --- the document's own language ------------------------------------------
$("html").attr({ lang: "en", dir: "ltr" });

// The canonical of the English page is the English page, and each version
// points at the other. Without this Google treats them as duplicates and
// picks one -- usually the wrong one for a given searcher.
const BASE = "https://tenders-finder.vercel.app";
$('link[rel="canonical"]').attr("href", `${BASE}/en`);
$('meta[property="og:url"]').attr("content", `${BASE}/en`);
$('meta[property="og:locale"]').remove();
$("head").append(
  `\n<meta property="og:locale" content="en_US">\n` +
    `<meta property="og:locale:alternate" content="he_IL">\n`
);

// The switcher has to say where the reader IS, and that is the opposite
// on this page. `aria-current` is what a screen reader announces, so
// moving it is the whole job -- the fill is CSS following the attribute.
$(".lang-switch a").removeAttr("aria-current");
$('.lang-switch a[hreflang="en"]').attr("aria-current", "true");
$(".lang-switch").attr("aria-label", "Language");

// These two are absolute site paths, so the climb below must not touch
// them -- and they are already correct for both editions.

// Relative links have to climb one level now that the page sits in /en/.
$("[href], [src]").each((_, el) => {
  const node = $(el);
  for (const attr of ["href", "src"]) {
    const value = node.attr(attr);
    if (!value) continue;
    const isRelativeFile = /^(?!https?:|mailto:|tel:|#|\/|data:)/.test(value);
    if (isRelativeFile) node.attr(attr, `../${value}`);
  }
});

// JSON-LD carries copy too, and it is a script tag so the walk skipped it.
$('script[type="application/ld+json"]').each((_, el) => {
  const node = $(el);
  let raw = node.html() || "";
  for (const [he, en] of Object.entries(dict)) raw = raw.split(he).join(en);
  raw = raw.split('"inLanguage": "he"').join('"inLanguage": "en"');
  node.html(raw);
});

if (CHECK) {
  console.log(`ok  ${swapped} strings translated, none missing`);
  process.exit(0);
}

mkdirSync(dirname(OUT), { recursive: true });
writeFileSync(OUT, $.html());
console.log(`en/index.html written — ${swapped} strings translated`);
