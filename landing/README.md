# Landing page

Single self-contained HTML file — no build step, no bundler. `index.html`
plus its static assets (`og.png`, `logo.png`, `favicon-32.png`,
`apple-touch-icon.png`) and the crawler files below. Serve the directory from
any static host.

```bash
npm run serve     # http://localhost:4173
```

## Deploying to Vercel

Vercel needs an account this repository cannot authenticate for you, so
these are run from your machine. Nothing here is built — Vercel serves the
directory as-is, with the headers in `vercel.json`.

```bash
cd landing
npx vercel login          # once
npx vercel deploy --prod  # answer: use current directory, framework "Other"
```

The deploy prints the URL. Then, once, point the sharing card at it:

```bash
npm run stamp -- https://<the-url-vercel-printed>
npx vercel deploy --prod
```

That second step exists because link previews are fetched by crawlers that
do not run JavaScript, so `og:image` has to be an absolute URL in the served
HTML — and the domain is not knowable until the deployment exists. Slack and
Facebook will resolve the relative one without it; X will not.

### Regenerating the sharing card

`og.png` is a rendered file, not a live one, so it goes stale silently when
the headline or the source list changes:

```bash
npm install     # playwright, used only by this script
npm run og
```

The committed `og.png` was rendered where no fonts could be fetched, so it
fell back to system faces. Running this once on a networked machine bakes it
in Heebo.

## Before it goes live

1. **Lead form endpoint.** `ENDPOINT` near the bottom of the script is empty.
   Until it is set, the form validates and then opens a prefilled email
   rather than pretending a submission was received. Set it to your own
   backend and the form POSTs JSON (`name`, `company`, `email`, `phone`,
   `sector`, `notes`).

   Submissions are deliberately **not** stored in the page. Contact details
   embedded in a shareable page are readable by everyone who opens it.

2. **`FALLBACK_EMAIL`** — set to `omeritzhaki10@gmail.com`. Deliberately a
   different address from the one the scraper service sends run digests
   to: this one is published in the page source of a public site and will
   be found by address harvesters, and the address that collects that
   should not be the one operational alerts arrive at.

   Verified by submitting the real form in a browser and capturing the
   `mailto:` the page navigates to — recipient, subject and all six body
   fields arrive intact, Hebrew included.

3. **The logo** is an SVG recreation of the supplied mark, so it stays crisp
   at any size and inherits no raster artefacts. Swap in the original asset
   if you have it as SVG; if you only have the PNG, keep this.

4. **Demo data** in `TENDERS` is representative, not live. The page says so
   in three places — the demo intro, the accessibility statement and the
   footer. Keep that labelling if you change the data.

## Standards

Built to IS 5568 / WCAG 2.2 AA. Verified in a browser, both themes:

- Contrast: body 17:1, secondary text 5.7–7.2:1, accent text 5.0–6.5:1,
  button fill 5.1–6.9:1 — all above the 4.5:1 minimum.
- Target size ≥24 px on every control (WCAG 2.2 §2.5.8).
- Single `h1`, no skipped heading levels across 26 headings.
- All 7 form controls have associated labels; errors are announced through a
  live region and focus moves to the first invalid field.
- Skip link is the first tab stop.
- `lang="he"`, `dir="rtl"`, landmarks present.
- `prefers-reduced-motion` stops the countdowns and all transitions.
- No horizontal overflow at 320 / 390 / 768 px.

The accent needs two tokens: `--blue` reads as text on the ground, and
`--blue-solid` sits behind white text. One value cannot pass AA for both —
they pull in opposite directions.


## Crawler and agent files

| File | What it is |
|---|---|
| `robots.txt` | Allows everything and points at the sitemap. |
| `sitemap.xml` | One entry, because the site is one page. |
| `llms.txt` | A description for LLM agents, including the accuracy notes that stop a summariser overstating what is built. |
| `404.html` | Served by Vercel with a 404 status. Marked `noindex`. |

Two things were deliberately **not** added, and should stay that way unless
the facts change:

- **`LocalBusiness` structured data.** This is a B2B SaaS with no premises
  the public visits. Declaring a local business in a machine-readable field
  would be a false claim, and search engines treat fabricated structured data
  as spam rather than as an error.
- **`BreadcrumbList`.** The site is a single page. Markup describing a
  hierarchy that does not exist is the same mistake in a different tag.

The canonical URL and the absolute `og:image` both hardcode
`https://tenders-finder.vercel.app`. **If a custom domain is ever attached,
both must be updated** — `npm run stamp -- https://your-domain` handles the
sharing card; the canonical tag, `og:url`, `robots.txt`, `sitemap.xml` and
`llms.txt` are currently by hand.
