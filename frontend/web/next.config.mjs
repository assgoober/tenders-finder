/**
 * @type {import('next').NextConfig}
 *
 * The response headers below are the ones the landing page has carried since
 * it was deployed (`frontend/landing/vercel.json`). The dashboard has had none of them,
 * for no better reason than that it had never been deployed anywhere — and it
 * is the half of the product that holds a session cookie and renders one
 * tenant's bid pipeline, so it is the half that needed them more.
 *
 * They are set here rather than in a `vercel.json` on purpose: this file is
 * read by `next dev` and by any host, so the headers are the same in
 * development as in production. A security header that only exists in the
 * deployed build is a header nobody ever tests.
 *
 * Note what is deliberately absent: a full Content-Security-Policy. The
 * landing page can carry one because it is a static file whose every script
 * is known. Next.js injects inline bootstrap scripts, so a real policy here
 * needs per-request nonces threaded through the middleware — and a CSP that
 * is slightly wrong does not fail loudly, it silently stops a script from
 * running. On this project that is a familiar shape of bug, and it is not one
 * to introduce on the way out the door. `frame-ancestors` below is the part
 * that can be set safely today, because it governs framing only and touches
 * no script.
 */
const nextConfig = {
  reactStrictMode: true,

  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          // Stop a browser from second-guessing a declared content type.
          { key: "X-Content-Type-Options", value: "nosniff" },
          // Send the origin, never the path, to another site. Tender detail
          // URLs carry a tender id, and the dashboard links out to buyer
          // portals on every screen.
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          // Nothing here needs any of these.
          {
            key: "Permissions-Policy",
            value: "camera=(), microphone=(), geolocation=(), interest-cohort=()",
          },
          // Clickjacking: the session is cookie-based, so a framed dashboard
          // is a framed authenticated session. X-Frame-Options is the legacy
          // spelling, kept for older browsers that ignore the CSP directive.
          { key: "Content-Security-Policy", value: "frame-ancestors 'none'" },
          { key: "X-Frame-Options", value: "DENY" },
        ],
      },
    ];
  },
};

export default nextConfig;
