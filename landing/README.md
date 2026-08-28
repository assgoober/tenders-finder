# Landing page

Single self-contained HTML file — no build step, no bundler. `index.html`
plus two static assets (`og.png`, `favicon.svg`). Serve the directory from
any static host.

```bash
npm run serve     # http://localhost:4173
```

## Deploying to Vercel

Vercel needs an account this repository cannot authenticate for you, so
these are run from your machine. Nothing here is built — Vercel serves the
directory as-is, with the headers in `vercel.json`.

```bash
cd tenders-finder/landing
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

2. **`FALLBACK_EMAIL`** — currently a placeholder address.

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
