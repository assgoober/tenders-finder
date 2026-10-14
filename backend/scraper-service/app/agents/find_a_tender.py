"""Find a Tender (UK) -- the second API-based agent.

Find a Tender is the UK's post-Brexit replacement for TED for above-threshold
public procurement, Ministry of Defence included. It publishes OCDS, the Open
Contracting Data Standard, over an anonymous JSON API: no key, no scraping, a
documented schema.

Three things about this source are not true of TED, and each one shapes the
code below.

**The API has no status filter.** `updatedFrom` and `updatedTo` are the only
parameters. A TED query can say "open tenders only" and be answered 774; here
the question has to be asked of every release we pull. Measured on a real page
of ten: one contract notice, one award update, seven award/contract pairs and
one planning notice. Ninety percent of the feed is procurement that has
already happened -- real data, useless to a bid team -- so the filter is not a
detail, it is most of the value.

**"Open" needs three fields, not one.** `tag` says what kind of document the
release is, `tender.status` says what state the procurement is in, and
`tenderPeriod.endDate` says when it actually closes. A release can be tagged
`tender` and still be `complete`. Trusting any one of them alone admits a
different wrong set each time.

**Deadlines are not uniformly timezone-aware.** Most carry an offset; some
publish a bare local datetime. Found by having a probe crash on the
comparison, not by reading the schema.

Pagination is a cursor in `links.next`, which is the shape we want: resumable,
and the server decides what "next" means rather than us computing an offset
into a result set that is changing underneath.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from ..config import Settings
from .base import FetchedTender, TenderSourceAgent, TenderStub
from .registry import register_agent

logger = logging.getLogger(__name__)


def parse_ocds_datetime(value: Any) -> datetime | None:
    """One OCDS datetime -> an aware datetime, or None.

    A naive value is read as UTC. That is wrong by at most an hour for a UK
    publisher, and an hour never decides whether a tender is worth looking at
    -- whereas crashing on the comparison ends the sweep.
    """
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def cpv_codes(tender: dict) -> list[str]:
    """Every CPV on the notice, main and additional.

    Deliberately both. The main classification is what the notice is filed
    under; the additional ones are what it also contains, and a defence lot
    inside a wider framework is routinely only in the second list.
    """
    codes: list[str] = []
    main = (tender.get("classification") or {}).get("id")
    if main:
        codes.append(str(main))
    for extra in tender.get("additionalClassifications") or []:
        code = extra.get("id")
        if code and str(code) not in codes:
            codes.append(str(code))
    return codes


@register_agent
class FindATenderAgent(TenderSourceAgent):
    key = "uk-fts"
    display_name = "Find a Tender (UK)"
    base_url = "https://www.find-tender.service.gov.uk"
    country = "GBR"
    sector = "all"

    API = "https://www.find-tender.service.gov.uk/api/1.0/ocdsReleasePackages"

    #: Set when the walk stopped for a reason other than running out of pages
    #: or hitting the caller's cap. Reported, never inferred.
    pagination_truncated: bool = False

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self._lookback_days = settings.fts_lookback_days
        self._page_size = settings.fts_page_size

    # --- Classification ----------------------------------------------------

    def is_open_notice(self, release: dict, now: datetime | None = None) -> tuple[bool, str]:
        """Is this a tender someone could still bid on?

        Returns the verdict AND the reason, so a sweep can report why it
        dropped nine releases in ten rather than only that it did.
        """
        now = now or datetime.now(timezone.utc)
        tender = release.get("tender") or {}
        tags = release.get("tag") or []

        # `tenderUpdate` is an amendment to a procurement that is still
        # running -- a changed deadline, a corrected specification. Dropping
        # it cost 63 of 264 candidate notices on a live 13-page walk, and the
        # ones it cost were the tenders whose original release predated our
        # window: precisely the ones nobody else would have told us about.
        #
        # It is safe to admit only because the row is keyed on the ocid: an
        # update to something we already hold upserts onto it rather than
        # competing with it.
        if not ({"tender", "tenderUpdate"} & set(tags)):
            # `award`, `contract`, `planning`, `awardUpdate`: real procurement
            # data, none of it something a bid team can act on.
            return False, "not a contract notice (%s)" % ("+".join(tags) or "untagged")

        status = tender.get("status")
        if status not in (None, "active"):
            return False, "status %s" % status

        deadline = parse_ocds_datetime((tender.get("tenderPeriod") or {}).get("endDate"))
        if deadline is None:
            # Unlike TED, where a missing deadline meant an award notice we
            # had already excluded, here it is genuinely unknown. A bid team
            # would rather see it than not.
            return True, "no deadline published"

        if deadline <= now:
            return False, "closed"
        return True, "open"

    # --- Navigation --------------------------------------------------------

    async def discover(self, *, limit: int = 50) -> list[TenderStub]:
        self.pagination_truncated = False
        now = datetime.now(timezone.utc)
        since = (now - timedelta(days=self._lookback_days)).strftime("%Y-%m-%dT00:00:00")
        url = "%s?updatedFrom=%s&limit=%d" % (self.API, since, self._page_size)

        stubs: list[TenderStub] = []
        dropped: dict[str, int] = {}
        pages = 0
        ended = "unknown"

        async with httpx.AsyncClient(
            timeout=self.settings.scrape_timeout_seconds,
            headers={
                "Accept": "application/json",
                "User-Agent": self.settings.scrape_user_agent,
            },
        ) as client:
            while url:
                if len(stubs) >= limit:
                    ended = "limit"
                    break

                response = await self.with_backoff(
                    lambda u=url: client.get(u), description="Find a Tender search"
                )
                if response.status_code >= 400:
                    # The body explains itself. Losing it to a bare status
                    # code is how attempts get spent guessing at an API that
                    # was willing to say what was wrong.
                    raise RuntimeError(
                        "Find a Tender returned %s: %s"
                        % (response.status_code, response.text[:300])
                    )

                body = response.json()
                releases = body.get("releases") or []
                if not releases:
                    ended = "exhausted"
                    break

                for release in releases:
                    ok, why = self.is_open_notice(release, now)
                    if not ok:
                        dropped[why] = dropped.get(why, 0) + 1
                        continue
                    stub = self.build_stub(release)
                    if stub is not None:
                        stubs.append(stub)

                pages += 1
                url = (body.get("links") or {}).get("next")
                if not url:
                    ended = "exhausted"
                    break

        if ended == "unknown":
            self.pagination_truncated = True
            logger.warning(
                "[%s] PARTIAL: %d open notices over %d pages, walk ended unexpectedly",
                self.key, len(stubs), pages,
            )

        total_dropped = sum(dropped.values())
        logger.info(
            "[%s] %d open notices from %d releases over %d pages",
            self.key, len(stubs), len(stubs) + total_dropped, pages,
        )
        for why, count in sorted(dropped.items(), key=lambda item: -item[1])[:5]:
            logger.info("[%s]   dropped %d: %s", self.key, count, why)

        return stubs[:limit]

    # --- Mapping -----------------------------------------------------------

    def build_stub(self, release: dict) -> TenderStub | None:
        """One OCDS release -> one TenderStub. Pure; tested without a network."""
        tender = release.get("tender") or {}
        # The ocid, not the notice id. An ocid is stable across the whole
        # lifecycle of one procurement; `id` changes every time the publisher
        # issues a release about it. Keying on `id` would have stored an
        # amended tender as a second, competing row for the same contract --
        # two deadlines, two scores, one of them quietly wrong.
        reference = release.get("ocid") or release.get("id")
        if not reference:
            # Without a stable id the row could never be deduplicated or
            # linked back, which makes it worse than absent.
            return None

        title = (tender.get("title") or release.get("description") or "").strip()
        if not title:
            return None

        value = tender.get("value") or {}

        return TenderStub(
            external_id=str(reference),
            title=title,
            source_url=self.notice_url(release),
            buyer_name=((release.get("buyer") or {}).get("name") or "").strip() or None,
            publication_date=parse_ocds_datetime(release.get("date")),
            deadline=parse_ocds_datetime((tender.get("tenderPeriod") or {}).get("endDate")),
            listing_metadata={
                "portal": self.key,
                "buyer_country": self.country,
                "ocid": release.get("ocid") or "",
                "notice_id": release.get("id"),
                "description": (tender.get("description") or "").strip() or None,
                "cpv_codes": cpv_codes(tender),
                "procurement_category": tender.get("mainProcurementCategory"),
                "procedure_type": tender.get("procurementMethodDetails"),
                "estimated_value": value.get("amount"),
                "estimated_value_currency": value.get("currency"),
                "notice_type": "+".join(release.get("tag") or []),
                "raw_status": tender.get("status"),
                # The change detector. OCDS gives a per-release `date` that
                # moves when the publisher edits the notice, so this is a real
                # fingerprint rather than a hash of the fields we happened to
                # map -- which would go stale the moment the mapping changed.
                "raw_row": "%s|%s|%s" % (reference, release.get("id"), release.get("date")),
            },
        )

    def notice_url(self, release: dict) -> str:
        """Where a human reads this notice.

        `links.self` is an API URL. A bid team needs the page, and the page is
        keyed on the ocid -- so the API link is deliberately not reused.
        """
        ocid = release.get("ocid")
        if ocid:
            return "%s/notice/%s" % (self.base_url, ocid)
        return "%s/Search/Results" % self.base_url

    async def fetch(self, stub: TenderStub) -> FetchedTender:
        """The listing already carries the notice text.

        OCDS puts title, description, selection criteria and contract terms in
        the release itself, so there is no second page to fetch. Under
        listing-first ingestion this is never called; it exists for the
        extraction path and for a future deep read of `tender.documents`.
        """
        meta = stub.listing_metadata or {}
        parts = [stub.title]
        if meta.get("description"):
            parts.append(meta["description"])
        return FetchedTender(stub=stub, raw_text="\n\n".join(parts))
