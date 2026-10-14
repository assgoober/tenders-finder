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
  console.error(`\nfix: add them to frontend/landing/content/en.json`);
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

// --- dates ----------------------------------------------------------------
//
// The Hebrew page writes 17.09.2026, and that string survived translation
// untouched because it contains no Hebrew to translate. On an English page it
// is ambiguous: a reader in the United States reads dd.mm as mm.dd, and on
// any day of the month up to the twelfth the misreading is silent -- the date
// is wrong and looks fine. Today it happened to be the seventeenth, which is
// the only reason it was legible at all.
//
// The `datetime` attribute is the authority, not the visible text: it is ISO,
// it is what `refresh-stats.mjs` writes, and it is unambiguous by definition.
// The month name is looked up from a table rather than formatted by
// `toLocaleDateString`, because `new Date("2026-09-17")` is UTC midnight and
// formatting it in a timezone behind UTC silently yields the sixteenth.
const MONTHS = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];
$("time[datetime]").each((_, el) => {
  const node = $(el);
  const iso = /^(\d{4})-(\d{2})-(\d{2})$/.exec(node.attr("datetime") || "");
  // A `datetime` that is not a plain calendar date -- a duration, a time --
  // is left exactly as it is rather than guessed at.
  if (!iso) return;
  const [, year, month, day] = iso;
  const name = MONTHS[Number(month) - 1];
  if (!name) return;
  node.text(`${Number(day)} ${name} ${year}`);
});

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

// --- the demo board ---------------------------------------------------
//
// The hero's tender feed is rendered by JavaScript from a `TENDERS` array
// and a handful of template strings, so the DOM walk above -- which skips
// <script> deliberately, to avoid rewriting code -- left all of it in
// Hebrew. 64 elements, in the most visible part of the page.
//
// Two mechanisms, and the difference matters. The ARRAY is swapped whole,
// between its own delimiters, because it is data: a regex over JS source
// would have to survive escaped quotes inside Hebrew strings (`ק\"מ`), and
// an extraction that silently splits a phrase in half is exactly the kind
// of clever fix this project keeps paying for. The UI STRINGS are replaced
// by exact match, which is safe for a different reason: Hebrew characters
// cannot appear in JavaScript syntax, so a Hebrew literal can only ever
// match a string.
const demoEn = readFileSync(`${HERE}/../content/demo.en.js`, "utf8");
const demoUi = JSON.parse(readFileSync(`${HERE}/../content/demo-ui.en.json`, "utf8"));

$("script").each((_, el) => {
  const node = $(el);
  if ((node.attr("type") || "").includes("ld+json")) return;
  let code = node.html();
  if (!code || !HEBREW.test(code)) return;

  const start = code.indexOf("  var TENDERS = [");
  if (start !== -1) {
    const end = code.indexOf("\n  ];", start);
    if (end === -1) {
      console.error("found `var TENDERS = [` but not its closing `  ];`");
      process.exit(1);
    }
      code = code.slice(0, start) + demoEn.trimEnd() + code.slice(end + ("\n  ];").length);
    swapped++;
  }

  // Longest first, and only on a Hebrew word boundary. Both halves were paid
  // for. Longest-first because "נסגר בקרוב" contains "נסגר", and the short
  // one landing first leaves "closed בקרוב". The boundary because a plain
  // substring replace of the two-letter word "יש" rewrote the middle of
  // "הקישור" into "הקThere areור" -- a corruption no test caught, which
  // surfaced only as garbled text inside a rendered listing. Hebrew has no
  // \b, so the boundary is explicit: the match must not be flanked by
  // another Hebrew letter.
  for (const he of Object.keys(demoUi).sort((a, b) => b.length - a.length)) {
    if (!code.includes(he)) continue;
    const escaped = he.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const bounded = new RegExp(
      "(?<![\\u0590-\\u05FF])" + escaped + "(?![\\u0590-\\u05FF])",
      "g"
    );
    const next = code.replace(bounded, demoUi[he]);
    if (next !== code) {
      code = next;
      swapped++;
    }
  }
  node.html(code);
});

// Nothing Hebrew may survive in a script on the English page. This is the
// check, not the hope: the two mechanisms above are each safe in their own
// way, and neither proves the other finished.
$("script").each((_, el) => {
  const node = $(el);
  if ((node.attr("type") || "").includes("ld+json")) return;
  // Comments are stripped first. They do not render, and translating them
  // would rewrite the explanations of why the code is the way it is --
  // including, in this very file, the comment that records the countdown
  // format "3ד 01:59" and why it needs an LTR wrapper.
  const source = (node.html() || "")
    .split("\n")
    .map((line) => line.replace(/\/\/.*$/, ""))
    .join("\n");
  const left = source.match(/[֐-׿][^'"<>\n]*/g);
  if (left) {
    console.error(`${left.length} Hebrew run(s) left in a script:`);
    for (const l of [...new Set(left)].slice(0, 10)) console.error(`  ${l.trim()}`);
    console.error("\nfix: add them to content/demo-ui.en.json");
    process.exit(1);
  }
});

// Does the page still RUN?
//
// The Hebrew check above proves the translation happened. It says nothing
// about whether the result is valid JavaScript -- and the first English
// build shipped a demo board that rendered nothing at all, because an
// English translation contained an apostrophe and the template it landed in
// was single-quoted. "No Hebrew left" was true; the page was broken.
//
// Parsing each script is the check that would have caught it. A translation
// writes into source code, so it has to be held to source code's standard.
$("script").each((_, el) => {
  const node = $(el);
  if ((node.attr("type") || "").includes("ld+json")) return;
  const code = node.html() || "";
  if (!code.trim()) return;
  try {
    new Function(code);
  } catch (err) {
    console.error(`the translated page does not parse: ${err.message}`);
    console.error(`
likely an apostrophe or quote in an English string.`);
    process.exit(1);
  }
});

// JSON-LD carries copy too, and it is a script tag so the walk skipped it.
$('script[type="application/ld+json"]').each((_, el) => {
  const node = $(el);
  let raw = node.html() || "";
  // Boundary-safe, for exactly the reason the demo pass is. A plain
  // split/join here rewrote the inside of Hebrew words with English ones and
  // produced "פלטפורמה לCollection מכרזי רכש" in the structured data --
  // machine-readable, indexed, and gibberish. Same bug, second place: I
  // fixed the first one and did not look for the others.
  for (const he of Object.keys(dict).sort((a, b) => b.length - a.length)) {
    const escaped = he.replace(/[.*+?^${}()|[\\]\\\\]/g, "\\$&");
    raw = raw.replace(
      new RegExp(`(?<![֐-׿])${escaped}(?![֐-׿])`, "g"),
      dict[he]
    );
  }
  raw = raw.split('"inLanguage": "he-IL"').join('"inLanguage": "en-GB"')
           .split('"inLanguage": "he"').join('"inLanguage": "en"');
  node.html(raw);
});

if (CHECK) {
  console.log(`ok  ${swapped} strings translated, none missing`);
  process.exit(0);
}

mkdirSync(dirname(OUT), { recursive: true });
writeFileSync(OUT, $.html());
console.log(`en/index.html written — ${swapped} strings translated`);
