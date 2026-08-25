"""Fixture-backed agent.

Serves realistic tender text from local files with no network access, so the
full pipeline — discover, fetch, extract, embed, persist, match — is runnable
and testable end to end before a single real portal is implemented.

This is also the reference for what a real agent must return.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .base import FetchedTender, TenderSourceAgent, TenderStub
from .registry import register_agent

_NOW = datetime.now(timezone.utc)

# Two very different tenders on purpose: one defense, one municipal. If the
# same pipeline and the same schema handle both without special-casing, the
# dual-use claim holds (REPOMIX.md ADR-1).
_FIXTURES: dict[str, dict] = {
    "MOD-2026-0417": {
        "title": "Supply of Ruggedized Tactical Radio Systems and Ancillaries",
        "buyer_name": "Directorate of Defense Procurement",
        "deadline": _NOW + timedelta(days=21),
        "text": """\
SOLICITATION No. MOD-2026-0417
Directorate of Defense Procurement — Communications Branch

1. SCOPE
The Directorate seeks proposals for the supply of 480 ruggedized handheld
tactical radio systems operating in the VHF/UHF bands, together with
vehicle-mount adapters, spare battery packs and a three-year support package.

2. CONDITIONS OF PARTICIPATION
2.1 The bidder shall hold a valid AS9100D certification at the time of
    submission. Certification obtained after the submission deadline shall
    not be considered.
2.2 The bidder shall hold facility security clearance at Level 3 or above.
2.3 The bidder shall demonstrate delivery of at least three (3) comparable
    tactical communications programmes within the preceding five (5) years,
    each of a value not less than USD 2,000,000.
2.4 Minimum annual turnover of USD 10,000,000 in each of the last three
    financial years.
2.5 A bid bond of 5% of the total offered price is required.
2.6 ISO 27001 certification is considered advantageous but is not a
    precondition for participation.

3. TECHNICAL REQUIREMENTS
3.1 Equipment shall comply with MIL-STD-810H for environmental conditions.
3.2 Equipment shall comply with MIL-STD-461G for electromagnetic
    compatibility.
3.3 Ingress protection of IP67 or better.
3.4 Operating temperature range of -30 degrees C to +60 degrees C.
3.5 AES-256 encryption with over-the-air rekeying.
3.6 Minimum battery endurance of 18 hours under a 5/5/90 duty cycle.

4. BILL OF QUANTITIES
    Item 1 — Handheld radio set, P/N TR-9500H          480 each
    Item 2 — Vehicle mount adapter kit, P/N VM-220      120 each
    Item 3 — Spare battery pack, P/N BP-9500           1440 each
    Item 4 — Three-year support and spares package        1 lot

Minimum order quantity for any subsequent call-off order: 24 units.

5. DELIVERY
Delivery DDP to the Central Logistics Depot within 240 days of contract
award. Partial deliveries are permitted in lots of not fewer than 60 units.

6. EVALUATION
Proposals will be evaluated on the following weighted basis:
    Technical compliance and performance     45%
    Price                                    35%
    Delivery schedule                        10%
    Local industrial participation           10%

7. AWARD
The contract will be awarded to the bidder achieving the highest total
weighted score. The Directorate intends to award a single contract, and
reserves the right to award no contract.

8. KEY DATES
    Clarification questions deadline: 14 days from publication
    Bid opening: 22 days from publication

9. OFFSET
Bidders shall specify proposed industrial cooperation of not less than 20%
of contract value.
""",
    },
    "MUNI-2026-0088": {
        "title": "Framework Agreement for Municipal Water Pipeline Rehabilitation",
        "buyer_name": "Metropolitan Water Authority",
        "deadline": _NOW + timedelta(days=35),
        "text": """\
TENDER REF: MUNI-2026-0088
Metropolitan Water Authority — Infrastructure Division

PURPOSE
Establishment of a four-year framework agreement for the rehabilitation of
municipal potable water distribution pipelines, including trenchless
relining, valve replacement and reinstatement works.

ELIGIBILITY
(a) Contractors shall be registered in Class C-4 or above for water and
    sewage works.
(b) Contractors shall hold ISO 9001:2015 and ISO 45001 certification.
(c) Contractors shall evidence completion of not less than 15 km of
    pipeline rehabilitation works in the last four years.
(d) Professional indemnity insurance of not less than EUR 2,000,000.

SCOPE OF WORKS
Estimated 60 km of DN100-DN400 ductile iron and asbestos-cement pipeline
over the framework term. Works shall comply with EN 805 and EN ISO 11295.
Cured-in-place lining materials shall be certified for potable water contact.

QUANTITIES (indicative, per annum)
    Trenchless relining, DN100-DN200      9,000 linear metres
    Trenchless relining, DN250-DN400      4,500 linear metres
    Gate valve replacement, DN150           220 each
    Surface reinstatement                 6,000 square metres

AWARD CRITERIA
Most economically advantageous tender: price 60%, technical methodology 25%,
environmental management plan 15%. The Authority intends to appoint up to
three contractors to the framework.

ESTIMATED VALUE
EUR 18,500,000 over the full four-year term.

SUBMISSION
Electronic submission only, closing 35 days from the date of this notice.
""",
    },
}


@register_agent
class SampleFixtureAgent(TenderSourceAgent):
    """Offline agent backed by in-repo fixtures."""

    key = "sample-fixtures"
    display_name = "Sample Fixtures (development)"
    base_url = "https://fixtures.local"
    country = None
    # Intentionally not "defense": the fixture set spans two sectors.
    sector = "mixed"

    async def discover(self, *, limit: int = 50) -> list[TenderStub]:
        stubs = [
            TenderStub(
                external_id=external_id,
                title=data["title"],
                source_url=f"{self.base_url}/tenders/{external_id}",
                buyer_name=data["buyer_name"],
                publication_date=_NOW,
                deadline=data["deadline"],
            )
            for external_id, data in _FIXTURES.items()
        ]
        return stubs[:limit]

    async def fetch(self, stub: TenderStub) -> FetchedTender:
        fixture = _FIXTURES.get(stub.external_id)
        if fixture is None:
            raise KeyError(f"no fixture for {stub.external_id}")
        return FetchedTender(stub=stub, raw_text=fixture["text"])
