# Brand assets

`logo-source.jpeg` is the artwork as the operator supplied it: the mark on an
opaque black field, 1254x1254, JPEG.

`logo.png` is that file with the black keyed out — 589x380 RGBA, aspect ratio
**1.567:1**. Everything the brand appears on is cut from this one file:

| Derived file | How |
|---|---|
| `landing/logo.png` | resized to 384px wide |
| `landing/favicon-32.png` | centred on a 32px transparent square |
| `landing/apple-touch-icon.png` | centred on a 180px transparent square |
| `landing/og.png` | rendered by `landing/tools/build-og.mjs` |

Two things worth knowing before you touch it.

**Do not redraw the mark as SVG.** A hand-traced approximation lived in the
page for a while. It was wrong in shape *and* in proportion — `1.269:1`
against the real `1.567:1` — and nobody noticed until the two were put side
by side. A raster that is right beats a vector that is close.

**Give it a height, let the width follow.** Fixing both dimensions is how the
proportion gets broken again.

The keying is an alpha ramp over `max(R,G,B)` between 10 and 30, with no
un-multiply: the source is a blend against black, so the edge pixels are
already darkened correctly and un-multiplying them would fringe the mark.
Roughly 2,000 pixels end up partially transparent, which is a clean one-pixel
edge — it composites without a halo on the near-black ground the site uses.
