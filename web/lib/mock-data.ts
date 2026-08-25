/**
 * Mock workspace data.
 *
 * The dashboard renders from this so it is visually testable before Supabase
 * exists. Swapping to live data means replacing `lib/api.ts` — the components
 * take `Tender[]` and never know where it came from.
 *
 * The set deliberately spans defense and civilian tenders, and includes one
 * `needs_review` record, so the UI is exercised against the states that
 * actually occur rather than only the happy path.
 */

import type { PipelineEntry, Tender, TenderMatch } from "./types";

function daysFromNow(days: number): string {
  const date = new Date();
  date.setDate(date.getDate() + days);
  date.setHours(12, 0, 0, 0);
  return date.toISOString();
}

function daysAgo(days: number): string {
  const date = new Date();
  date.setDate(date.getDate() - days);
  return date.toISOString().slice(0, 10);
}

export const MOCK_TENDERS: Tender[] = [
  {
    id: "t-1",
    title: "Supply of Ruggedized Tactical Radio Systems and Ancillaries",
    summary:
      "Supply of 480 handheld VHF/UHF tactical radios with vehicle-mount adapters, spare batteries and a three-year support package.",
    buyer_name: "Directorate of Defense Procurement",
    source_url: "https://example.gov/tenders/MOD-2026-0417",
    source_name: "MOD Procurement Portal",
    publication_date: daysAgo(4),
    deadline: daysFromNow(17),
    status: "open",
    estimated_value: 14200000,
    currency: "USD",
    extraction_status: "extracted",
    metadata: {
      reference_number: "MOD-2026-0417",
      language: "en",
      extraction_confidence: "high",
      unclear_sections: [],
      participation: {
        eligibility_summary:
          "Open to cleared suppliers holding aerospace quality certification and demonstrable tactical communications delivery experience.",
        mandatory_prerequisites: [
          "Valid AS9100D certification held at the time of submission",
          "Facility security clearance at Level 3 or above",
          "Three comparable programmes in the preceding five years, each not less than USD 2,000,000",
          "Minimum annual turnover of USD 10,000,000 in each of the last three financial years",
          "Bid bond of 5% of the total offered price",
        ],
        required_certifications: [
          { name: "AS9100D", issuing_body: "IAQG", mandatory: true },
          { name: "ISO 27001", issuing_body: "ISO", mandatory: false },
        ],
        required_experience:
          "Three comparable tactical communications programmes within five years.",
        financial_requirements:
          "Turnover of USD 10,000,000 per year for three years; 5% bid bond.",
        geographic_restrictions: null,
      },
      evaluation: {
        method: "Weighted score across technical, price, schedule and local content",
        price_weight_percent: 35,
        criteria: [
          { criterion: "Technical compliance and performance", weight_percent: 45, description: null },
          { criterion: "Price", weight_percent: 35, description: null },
          { criterion: "Delivery schedule", weight_percent: 10, description: null },
          { criterion: "Local industrial participation", weight_percent: 10, description: null },
        ],
      },
      guidelines: {
        scope_summary:
          "480 handheld VHF/UHF tactical radio sets with mounts, spares and three-year support.",
        technical_specs: [
          "Ingress protection IP67 or better",
          "Operating range -30°C to +60°C",
          "AES-256 encryption with over-the-air rekeying",
          "Battery endurance of 18 hours at a 5/5/90 duty cycle",
        ],
        referenced_standards: ["MIL-STD-810H", "MIL-STD-461G"],
        line_items: [
          { description: "Handheld radio set", quantity: 480, unit: "each", part_number: "TR-9500H" },
          { description: "Vehicle mount adapter kit", quantity: 120, unit: "each", part_number: "VM-220" },
          { description: "Spare battery pack", quantity: 1440, unit: "each", part_number: "BP-9500" },
          { description: "Three-year support and spares package", quantity: 1, unit: "lot", part_number: null },
        ],
        minimum_order_quantity: 24,
        delivery_terms:
          "DDP to Central Logistics Depot within 240 days of award; partial deliveries in lots of not fewer than 60 units.",
        contract_duration: "Three-year support term",
        warranty_terms: null,
      },
      selection: {
        award_basis: "Highest total weighted score",
        decisive_metric: "Technical compliance and performance (45%)",
        number_of_awards: 1,
        framework_agreement: false,
      },
      key_dates: [
        { label: "Clarification questions deadline", date: daysFromNow(10) },
        { label: "Bid opening", date: daysFromNow(18) },
      ],
      sector_specific: {
        security_clearance_level: "Level 3",
        offset_obligation_percent: 20,
      },
    },
  },
  {
    id: "t-2",
    title: "Framework Agreement for Municipal Water Pipeline Rehabilitation",
    summary:
      "Four-year framework for trenchless rehabilitation of DN100–DN400 potable water mains, valve replacement and reinstatement.",
    buyer_name: "Metropolitan Water Authority",
    source_url: "https://example.gov/tenders/MUNI-2026-0088",
    source_name: "Municipal Tenders Board",
    publication_date: daysAgo(2),
    deadline: daysFromNow(33),
    status: "open",
    estimated_value: 18500000,
    currency: "EUR",
    extraction_status: "extracted",
    metadata: {
      reference_number: "MUNI-2026-0088",
      language: "en",
      extraction_confidence: "high",
      unclear_sections: [],
      participation: {
        eligibility_summary:
          "Open to contractors registered Class C-4 or above for water and sewage works.",
        mandatory_prerequisites: [
          "Registration in Class C-4 or above for water and sewage works",
          "Completion of not less than 15 km of pipeline rehabilitation in the last four years",
          "Professional indemnity insurance of not less than EUR 2,000,000",
        ],
        required_certifications: [
          { name: "ISO 9001:2015", issuing_body: "ISO", mandatory: true },
          { name: "ISO 45001", issuing_body: "ISO", mandatory: true },
        ],
        required_experience: "15 km of pipeline rehabilitation works in four years.",
        financial_requirements: "Professional indemnity insurance of EUR 2,000,000.",
        geographic_restrictions: null,
      },
      evaluation: {
        method: "Most economically advantageous tender",
        price_weight_percent: 60,
        criteria: [
          { criterion: "Price", weight_percent: 60, description: null },
          { criterion: "Technical methodology", weight_percent: 25, description: null },
          { criterion: "Environmental management plan", weight_percent: 15, description: null },
        ],
      },
      guidelines: {
        scope_summary:
          "Approximately 60 km of DN100–DN400 ductile iron and asbestos-cement pipeline over four years.",
        technical_specs: [
          "Cured-in-place lining materials certified for potable water contact",
        ],
        referenced_standards: ["EN 805", "EN ISO 11295"],
        line_items: [
          { description: "Trenchless relining, DN100–DN200", quantity: 9000, unit: "linear metres per annum", part_number: null },
          { description: "Trenchless relining, DN250–DN400", quantity: 4500, unit: "linear metres per annum", part_number: null },
          { description: "Gate valve replacement, DN150", quantity: 220, unit: "each per annum", part_number: null },
          { description: "Surface reinstatement", quantity: 6000, unit: "square metres per annum", part_number: null },
        ],
        minimum_order_quantity: null,
        delivery_terms: null,
        contract_duration: "Four years",
        warranty_terms: null,
      },
      selection: {
        award_basis: "Most economically advantageous tender",
        decisive_metric: "Price (60%)",
        number_of_awards: 3,
        framework_agreement: true,
      },
      key_dates: [{ label: "Electronic submission closes", date: daysFromNow(33) }],
      sector_specific: {
        works_class: "C-4",
        potable_water_contact: true,
      },
    },
  },
  {
    id: "t-3",
    title: "Procurement of Counter-UAS Detection and Tracking Systems",
    summary:
      "Acquisition of fixed and mobile counter-unmanned-aircraft detection systems for perimeter protection at three sites.",
    buyer_name: "National Airports Security Authority",
    source_url: "https://example.gov/tenders/NASA-CUAS-2026-11",
    source_name: "National eProcurement",
    publication_date: daysAgo(11),
    deadline: daysFromNow(4),
    status: "closing_soon",
    estimated_value: 6800000,
    currency: "EUR",
    extraction_status: "extracted",
    metadata: {
      reference_number: "NASA-CUAS-2026-11",
      language: "en",
      extraction_confidence: "medium",
      unclear_sections: ["Annex D — spectrum licensing conditions (partially illegible scan)"],
      participation: {
        eligibility_summary:
          "Open to EU-established suppliers holding national security vetting for critical infrastructure work.",
        mandatory_prerequisites: [
          "Supplier established in an EU member state",
          "National security vetting for critical infrastructure",
        ],
        required_certifications: [{ name: "ISO 27001", issuing_body: "ISO", mandatory: true }],
        required_experience: null,
        financial_requirements: null,
        geographic_restrictions: "Suppliers must be established within the EU.",
      },
      evaluation: {
        method: "Quality/price 70:30",
        price_weight_percent: 30,
        criteria: [
          { criterion: "Detection performance and false-alarm rate", weight_percent: 40, description: null },
          { criterion: "Integration with existing C2 systems", weight_percent: 30, description: null },
          { criterion: "Price", weight_percent: 30, description: null },
        ],
      },
      guidelines: {
        scope_summary: "Fixed and mobile C-UAS detection and tracking for three airport perimeters.",
        technical_specs: [
          "Detection of Group 1 and Group 2 UAS at not less than 5 km",
          "Passive RF detection with optional radar augmentation",
        ],
        referenced_standards: ["EN 303 883"],
        line_items: [
          { description: "Fixed detection node", quantity: 12, unit: "each", part_number: null },
          { description: "Mobile detection vehicle kit", quantity: 3, unit: "each", part_number: null },
        ],
        minimum_order_quantity: null,
        delivery_terms: "Delivery and commissioning within 180 days of award.",
        contract_duration: null,
        warranty_terms: "24 months from acceptance",
      },
      selection: {
        award_basis: "Highest quality-weighted score",
        decisive_metric: "Detection performance (40%)",
        number_of_awards: 1,
        framework_agreement: false,
      },
      key_dates: [{ label: "Site visit", date: daysFromNow(1) }],
      sector_specific: {
        spectrum_licence_required: true,
      },
    },
  },
  {
    id: "t-4",
    title: "Maintenance, Repair and Overhaul Services for Rotary-Wing Fleet",
    summary: null,
    buyer_name: "Joint Air Support Command",
    source_url: "https://example.gov/tenders/JASC-MRO-2026-03",
    source_name: "MOD Procurement Portal",
    publication_date: daysAgo(19),
    deadline: daysFromNow(-3),
    status: "closed",
    estimated_value: null,
    currency: null,
    // Scanned PDF with no text layer: two constrained attempts failed, so it
    // was routed to a human instead of persisting a guess (REPOMIX.md ADR-2).
    extraction_status: "needs_review",
    metadata: {
      reference_number: "JASC-MRO-2026-03",
      language: null,
      extraction_confidence: "low",
      unclear_sections: [
        "Entire document — scanned without a text layer, OCR required",
      ],
      participation: {
        eligibility_summary: null,
        mandatory_prerequisites: [],
        required_certifications: [],
        required_experience: null,
        financial_requirements: null,
        geographic_restrictions: null,
      },
      evaluation: { method: null, criteria: [], price_weight_percent: null },
      guidelines: {
        scope_summary: null,
        technical_specs: [],
        referenced_standards: [],
        line_items: [],
        minimum_order_quantity: null,
        delivery_terms: null,
        contract_duration: null,
        warranty_terms: null,
      },
      selection: {
        award_basis: null,
        decisive_metric: null,
        number_of_awards: null,
        framework_agreement: null,
      },
      key_dates: [],
      sector_specific: {},
    },
  },
];

export const MOCK_MATCHES: TenderMatch[] = [
  {
    tender_id: "t-1",
    profile_name: "Tactical Communications",
    score: 0.91,
    matched_keywords: ["MIL-STD-810H", "VHF", "AES-256"],
  },
  {
    tender_id: "t-3",
    profile_name: "Perimeter Security Systems",
    score: 0.78,
    matched_keywords: ["C-UAS", "RF detection"],
  },
];

export const MOCK_PIPELINE: PipelineEntry[] = [
  { tender_id: "t-1", status: "evaluating", owner: "R. Cohen", notes: null },
  { tender_id: "t-2", status: "new", owner: null, notes: null },
  { tender_id: "t-3", status: "drafting", owner: "A. Levi", notes: "Waiting on C2 integration quote." },
  { tender_id: "t-4", status: "no_bid", owner: null, notes: "Deadline passed." },
];
