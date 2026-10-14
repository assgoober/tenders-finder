import type { Metadata } from "next";
import { Inter } from "next/font/google";

import "./globals.css";

const inter = Inter({ subsets: ["latin"], variable: "--font-sans" });

export const metadata: Metadata = {
  title: "Tenders Finder",
  description:
    "Unified workspace for defense and public procurement tenders: automated aggregation, AI extraction, and semantic alerts.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    // The font variable must sit on <html>: Tailwind's base styles set
    // font-family on <html> from var(--font-sans), and a variable defined
    // only on <body> is undefined there — the whole rule is dropped and every
    // page falls back to the browser's serif font.
    <html lang="en" className={inter.variable}>
      <body>{children}</body>
    </html>
  );
}
