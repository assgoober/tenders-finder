"""TED agent tests.

Every fixture here is a real response shape captured from the live API on
2026-09-11, not one invented to match the parser. The two defects these cover
were both found by reading actual notices rather than the documentation:
notices with no tender deadline, and a feed that is 47% contract *award*
notices.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.agents.ted import (
    TED_FIELDS,
    TedAgent,
    combine_deadline,
    first_value,
    notice_url,
    parse_ted_date,
    pick_language,
    resolve_deadline,
    xml_url,
)
from app.config import Settings


SETTINGS = Settings(embedding_provider="hash")

# Captured verbatim from POST /v3/notices/search.
REAL_NOTICE = {
    "publication-number": "626290-2026",
    "notice-type": "cn-standard",
    "buyer-country": ["DEU"],
    "buyer-name": {"deu": ["Stadt Wolfenbuettel"]},
    "classification-cpv": ["34144210", "35412200", "34144210"],
    "deadline-receipt-tender-date-lot": ["2026-10-13+02:00"],
    "deadline-receipt-tender-time-lot": ["09:00:00+02:00"],
    "notice-title": {
        "eng": "Germany - Firefighting vehicles - Beschaffung von fuenf MTW",
        "deu": "Deutschland - Feuerwehrfahrzeuge",
    },
    "publication-date": "2026-09-11+02:00",
    "links": {"xml": {"MUL": "https://ted.europa.eu/en/notice/626290-2026/xml"}},
}


# --- multilingual fields ---------------------------------------------------

def test_a_multilingual_field_resolves_to_english():
    """These arrive as {lang: [values]}. Stored raw, the database gets a
    Python dict repr where a title should be."""
    assert pick_language(REAL_NOTICE["notice-title"]).startswith("Germany")


def test_a_notice_with_no_english_is_kept_in_its_own_language():
    """A German-only defence notice is still a defence notice. Dropping it
    would lose real procurement to a formatting preference."""
    assert pick_language({"deu": ["Feuerwehrfahrzeuge"]}) == "Feuerwehrfahrzeuge"


def test_absent_and_blank_are_both_none():
    """The pipeline stores None; a blank string would be embedded as if it
    were real text and would pollute the vector."""
    assert pick_language(None) is None
    assert pick_language({"eng": ["   "]}) is None
    assert pick_language([]) is None
    assert pick_language({}) is None


def test_a_plain_string_passes_through():
    assert pick_language("already text") == "already text"


# --- dates -----------------------------------------------------------------

def test_a_date_keeps_its_day_and_discards_the_offset():
    assert parse_ted_date(["2026-10-13+02:00"]).isoformat() == "2026-10-13"


def test_an_unparseable_date_is_none_not_an_exception():
    assert parse_ted_date(["not a date"]) is None
    assert parse_ted_date([]) is None
    assert parse_ted_date(None) is None


def test_the_deadline_keeps_its_time():
    """The failure this guards is the one the BALAM parser had: a date with
    the time dropped is a perfectly valid timestamp, hours early."""
    got = combine_deadline(["2026-10-13+02:00"], ["09:00:00+02:00"])
    assert got.hour == 9 and got.minute == 0
    assert got.utcoffset() is not None, "the offset must survive"


def test_a_date_with_no_published_time_is_not_given_an_invented_one():
    got = combine_deadline(["2026-10-13+02:00"], [])
    assert got == datetime(2026, 10, 13, 0, 0)


# --- which deadline ---------------------------------------------------------

def test_an_open_procedure_uses_the_tender_deadline():
    when, kind = resolve_deadline(REAL_NOTICE)
    assert kind == "tender" and when.hour == 9


def test_a_restricted_procedure_falls_back_to_the_request_deadline():
    """Five notices in a forty-notice sample had no tender deadline and a
    request-to-participate deadline instead. Reading only the first reported
    None and dropped the date a bidder actually has to hit."""
    notice = {
        "publication-number": "626989-2026",
        "deadline-receipt-request-date-lot": ["2026-10-19+02:00"],
        "deadline-receipt-request-time-lot": ["13:00:00+02:00"],
    }
    when, kind = resolve_deadline(notice)
    assert kind == "request-to-participate"
    assert when.hour == 13


def test_the_two_gates_stay_distinguishable():
    """Flattening them into one unlabelled timestamp would tell a bidder the
    wrong thing about what closes."""
    when, kind = resolve_deadline({})
    assert (when, kind) == (None, None)


# --- stub construction ------------------------------------------------------

def test_a_real_notice_becomes_a_complete_stub():
    agent = TedAgent(SETTINGS)
    stub = agent.build_stub(REAL_NOTICE)

    assert stub.external_id == "626290-2026"
    assert stub.title.startswith("Germany")
    assert stub.buyer_name == "Stadt Wolfenbuettel"
    assert stub.source_url == "https://ted.europa.eu/en/notice/-/detail/626290-2026"
    assert stub.deadline.hour == 9
    assert stub.listing_metadata["buyer_country"] == "DEU"
    assert stub.listing_metadata["deadline_kind"] == "tender"
    assert stub.listing_metadata["xml_url"].endswith("/xml")


def test_a_notice_with_no_publication_number_is_dropped():
    """Without it there is no stable external_id and no URL, so the row could
    never be deduplicated or linked back to its source."""
    assert TedAgent(SETTINGS).build_stub({"notice-title": {"eng": "orphan"}}) is None


def test_the_change_detector_moves_when_the_deadline_moves():
    """raw_row is what the pipeline's incremental skip compares. TED has no
    last-updated column, so the deadline has to carry that signal."""
    agent = TedAgent(SETTINGS)
    before = agent.build_stub(REAL_NOTICE).listing_metadata["raw_row"]

    moved = dict(REAL_NOTICE, **{"deadline-receipt-tender-date-lot": ["2026-11-20+02:00"]})
    after = agent.build_stub(moved).listing_metadata["raw_row"]

    assert before != after


# --- query ------------------------------------------------------------------

def test_award_notices_are_excluded_by_default():
    """47% of the raw defence-CPV feed is `can-standard` -- contract AWARD
    notices, announcing a deal already done. Real procurement data, and
    useless to a bid team."""
    query = TedAgent(SETTINGS).build_query()
    assert "notice-type IN (cn-standard)" in query


def test_the_query_is_built_from_configuration_not_hardcoded():
    """A customer who makes field kitchens wants 39000000 too, and that is a
    product decision rather than a code change."""
    agent = TedAgent(Settings(embedding_provider="hash",
                              ted_cpv_codes=[35000000, 39000000],
                              ted_lookback_days=7))
    query = agent.build_query()
    assert "35000000, 39000000" in query
    assert "today(-7)" in query


def test_newest_first_so_a_capped_run_keeps_what_is_actionable():
    """The archive reaches back to 2016. Without the sort, a run capped by
    `limit` returns an arbitrary slice of history."""
    assert TedAgent(SETTINGS).build_query().endswith("SORT BY publication-date DESC")


def test_the_requested_field_list_stays_small():
    """The API exposes 1,830 field names. Asking for everything moves
    megabytes per page to populate columns nothing reads."""
    assert len(TED_FIELDS) < 25
    assert "publication-number" in TED_FIELDS


# --- links ------------------------------------------------------------------

def test_the_xml_link_prefers_the_multilingual_document():
    links = {"xml": {"ENG": "https://x/eng", "MUL": "https://x/mul"}}
    assert xml_url(links) == "https://x/mul"


def test_missing_links_do_not_raise():
    assert xml_url(None) is None
    assert xml_url({}) is None
    assert notice_url(None, None) is None


def test_first_value_unwraps_one_level_only():
    assert first_value(["a", "b"]) == "a"
    assert first_value("a") == "a"
    assert first_value([]) is None


# --- recall vs precision ----------------------------------------------------

def test_the_cpv_field_is_a_dial_not_a_constant():
    """Measured over the same 30-day window: classification-cpv returns 623
    notices, main-classification-lot returns 394. The 229-notice difference is
    things like a hospital supply order carrying one protective-clothing code
    among twenty-eight."""
    broad = TedAgent(SETTINGS).build_query()
    assert "classification-cpv IN" in broad

    narrow = TedAgent(Settings(embedding_provider="hash",
                               ted_cpv_field="main-classification-lot")).build_query()
    assert "main-classification-lot IN" in narrow


def test_the_default_harvests_wide():
    """Precision is not this stage's job. The matching engine scores every
    tender against a capability profile downstream, so an irrelevant notice
    ranks low and costs a row -- while a defence lot dropped at ingestion is
    gone for good."""
    assert SETTINGS.ted_cpv_field == "classification-cpv"


# --- pagination -------------------------------------------------------------

class _FakeResponse:
    def __init__(self, payload):
        self.status_code = 200
        self._payload = payload
    def json(self):
        return self._payload


class _FakeClient:
    """Records what was asked for and serves scripted pages."""

    def __init__(self, pages):
        self.pages = list(pages)
        self.payloads = []

    async def __aenter__(self):
        return self
    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None, **kw):
        self.payloads.append(json)
        return _FakeResponse(self.pages.pop(0) if self.pages else
                             {"notices": [], "totalNoticeCount": 0})


def _notice(i):
    return {"publication-number": f"{i}-2026", "notice-title": {"eng": f"n{i}"}}


@pytest.fixture
def fake_ted(monkeypatch):
    def _install(pages):
        client = _FakeClient(pages)
        monkeypatch.setattr("app.agents.ted.httpx.AsyncClient", lambda **kw: client)
        return client
    return _install


@pytest.mark.asyncio
async def test_the_request_asks_for_the_continuation_token(fake_ted):
    """Without paginationMode the service answers page one and returns no
    iterationNextToken at all -- so a loop that stops when the token is
    missing stops after 100 notices and calls it a complete sweep. The same
    BALAM pagination failure, in an agent that has no pager to get wrong."""
    client = fake_ted([{"notices": [_notice(1)], "totalNoticeCount": 1}])
    await TedAgent(SETTINGS).discover(limit=10)
    assert client.payloads[0]["paginationMode"] == "ITERATION"


@pytest.mark.asyncio
async def test_the_walk_follows_the_token_across_pages(fake_ted):
    client = fake_ted([
        {"notices": [_notice(1), _notice(2)], "iterationNextToken": "t1", "totalNoticeCount": 3},
        {"notices": [_notice(3)], "iterationNextToken": "t2"},
        {"notices": [], "iterationNextToken": "t3"},
    ])
    agent = TedAgent(SETTINGS)
    stubs = await agent.discover(limit=100)

    assert [s.external_id for s in stubs] == ["1-2026", "2-2026", "3-2026"]
    assert client.payloads[1]["iterationNextToken"] == "t1"
    assert agent.pagination_truncated is False


@pytest.mark.asyncio
async def test_an_empty_page_ends_the_walk_even_with_a_token(fake_ted):
    """Measured live: the service keeps handing back a token after the last
    row, so an empty page -- not a missing token -- is how it actually ends."""
    agent = TedAgent(SETTINGS)
    fake_ted([
        {"notices": [_notice(1)], "iterationNextToken": "t1", "totalNoticeCount": 623},
        {"notices": [], "iterationNextToken": "still-here"},
    ])
    await agent.discover(limit=100)
    assert agent.pagination_truncated is False


@pytest.mark.asyncio
async def test_a_short_harvest_is_not_partial_just_because_the_count_disagrees(fake_ted):
    """TED reports 623 available and serves 551 across a full iteration.
    Keying the guard on its own arithmetic would fire on every complete run,
    and a guard that always fires is one nobody reads."""
    agent = TedAgent(SETTINGS)
    fake_ted([
        {"notices": [_notice(1)], "iterationNextToken": "t1", "totalNoticeCount": 623},
        {"notices": []},
    ])
    await agent.discover(limit=1000)
    assert agent.pagination_truncated is False


@pytest.mark.asyncio
async def test_hitting_the_caller_cap_is_not_a_truncation(fake_ted):
    agent = TedAgent(SETTINGS)
    fake_ted([{"notices": [_notice(i) for i in range(5)],
               "iterationNextToken": "t1", "totalNoticeCount": 623}])
    stubs = await agent.discover(limit=3)
    assert len(stubs) == 3
    assert agent.pagination_truncated is False
