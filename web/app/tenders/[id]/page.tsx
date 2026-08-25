/**
 * Deep-linkable tender page.
 *
 * The side panel is the primary way to read a tender, but an alert email or
 * a message to a colleague needs a URL that opens straight to one — so the
 * same extraction is addressable on its own route.
 */

import Link from "next/link";
import { notFound } from "next/navigation";

import { TenderDetail } from "@/components/tender-detail";
import { MOCK_TENDERS } from "@/lib/mock-data";

export function generateStaticParams() {
  return MOCK_TENDERS.map((tender) => ({ id: tender.id }));
}

export default function TenderPage({ params }: { params: { id: string } }) {
  const tender = MOCK_TENDERS.find((entry) => entry.id === params.id);
  if (!tender) notFound();

  return (
    <main className="mx-auto max-w-4xl px-6 py-8">
      <Link href="/" className="text-sm text-sky-700 underline-offset-4 hover:underline">
        ← Back to workspace
      </Link>
      <div className="mt-4">
        <TenderDetail tender={tender} />
      </div>
    </main>
  );
}
