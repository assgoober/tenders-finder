/**
 * Render tools/og-card.html to og.png (1200×630, the size every link
 * unfurler expects).
 *
 *   node tools/build-og.mjs
 *
 * The committed og.png was rendered in a sandbox with no outbound network,
 * so its webfonts fell back to system faces. Run this once on a machine that
 * can reach fonts.googleapis.com to bake the card in Heebo, and re-run it
 * whenever the headline or the source list changes — the card is a second
 * copy of that copy, and a stale one is worse than none.
 */
import { chromium } from "playwright";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const source = resolve(here, "og-card.html");
const target = resolve(here, "..", "og.png");

const browser = await chromium.launch();
const page = await browser.newPage({
  viewport: { width: 1200, height: 630 },
  deviceScaleFactor: 1,
});
await page.goto("file://" + source);
// Wait for the webfonts rather than a fixed delay: the whole reason to run
// this on a networked machine is to get the real faces into the raster.
await page.evaluate(() => document.fonts.ready);
await page.waitForTimeout(400);
await page.screenshot({ path: target });
await browser.close();

console.log("wrote " + target);
