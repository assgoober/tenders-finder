"""TED agent tests.

Every fixture here is a real response shape captured from the live API on
2026-09-11, not one invented to match the parser. The two defects these cover
were both found by reading actual notices rather than the documentation:
notices with no tender deadline, and a feed that is 47% contract *award*
notices.
"""

from __future__ import annotations

from datetime import datetime

import re

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


SETTINGS = Settings(
    embedding_provider="hash",
    # The real sweep waits three seconds between shards so TED does not
    # answer with 429. There are 31 divisions by default, so a test that
    # takes the production value sleeps for 93 seconds -- which is what
    # made this whole file time out while every test in it passed when run
    # alone. A test that sleeps is a test nobody runs.
    ted_shard_pause_seconds=0,
)

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
                              ted_divisions_per_query=0))
    query = agent.build_query()
    assert "35000000, 39000000" in query

    # `ted_lookback_days` only shapes the backfill path -- the open-only
    # default has no publication window to size.
    backfill = TedAgent(Settings(embedding_provider="hash",
                                 ted_cpv_codes=[35000000, 39000000],
                                 ted_open_only=False,
                                 ted_lookback_days=7))
    assert "today(-7)" in backfill.build_query()


def test_the_query_never_sorts_on_a_deadline_field():
    """TED refuses to, and finding that out cost a whole sweep.

        SORT BY deadline-receipt-tender-date-lot ASC
        -> 400 QUERY_SYNTAX_ERROR, "extraneous input 'ASC'"

    The filter had been probed against the live API; the sort had not. Every
    WHERE clause was right and all thirty-one shards died on the ORDER BY.
    The ordering a reader needs is applied after ingestion, on a column we
    own.
    """
    q = TedAgent(SETTINGS).build_query()
    assert "SORT BY deadline" not in q
    assert q.endswith("SORT BY publication-date DESC")


def test_the_backfill_path_still_sorts_newest_first():
    """Walking history has no deadlines to sort by -- everything in it is
    closed, which is the point of walking it."""
    from app.config import Settings
    agent = TedAgent(Settings(extraction_provider="gemini", gemini_api_key="k",
                              embedding_provider="hash", ted_open_only=False))
    assert agent.build_query().endswith("SORT BY publication-date DESC")


def test_open_only_asks_for_both_deadline_fields():
    """A restricted procedure carries only a request-to-participate deadline
    until its shortlist exists. Keying on the tender deadline alone silently
    drops every two-stage procurement -- which is most large defence work.
    Measured: 603 notices one way, 774 with both."""
    q = TedAgent(SETTINGS).build_query()
    assert "deadline-receipt-tender-date-lot>=today(0)" in q
    assert "deadline-receipt-request-date-lot>=today(0)" in q
    assert " OR " in q


def test_open_only_does_not_filter_on_publication_date():
    """The two filters are not approximations of each other. Measured on
    defence CPV: the 30-day publication window missed 181 open tenders and
    paid for 62 closed ones.

    The field still appears in the SORT, which is a different job -- keeping
    the ITERATION walk a stable partition. This asserts on the WHERE.
    """
    where = TedAgent(SETTINGS).build_query().split(" SORT BY ")[0]
    assert "publication-date" not in where


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


# --- sharding the sweep by CPV division ------------------------------------
#
# A sweep of fourteen divisions is fourteen walks, not one large request.
# These pin the three properties that makes worth doing.

def _sharded(**over):
    from app.config import Settings
    from app.agents.ted import TedAgent
    base = dict(
        extraction_provider="gemini", gemini_api_key="k",
        embedding_provider="hash",
        ted_cpv_codes=[35000000, 34000000, 72000000],
        ted_divisions_per_query=1,
        # The real sweep waits between shards so TED does not answer with
        # 429. A test that waits is a test nobody runs.
        ted_shard_pause_seconds=0,
    )
    base.update(over)
    return TedAgent(Settings(**base))


def test_each_division_gets_its_own_query():
    agent = _sharded()
    assert agent.shards() == [[35000000], [34000000], [72000000]]


def test_divisions_can_be_grouped():
    agent = _sharded(ted_divisions_per_query=2)
    assert agent.shards() == [[35000000, 34000000], [72000000]]


def test_zero_means_one_query_for_everything():
    """The old behaviour, kept reachable rather than deleted."""
    agent = _sharded(ted_divisions_per_query=0)
    assert agent.shards() == [[35000000, 34000000, 72000000]]


def test_a_shard_query_asks_only_for_its_own_division():
    agent = _sharded()
    q = agent.build_query([34000000])
    assert "34000000" in q
    assert "35000000" not in q, "a shard must not carry its neighbours"


# --- the window is a band, not a horizon -----------------------------------

def test_the_default_window_has_no_upper_edge():
    """Offset 0 means "the last 30 days", which needs one bound, not two."""
    q = _sharded(ted_open_only=False).build_query()
    assert "publication-date>=today(-30)" in q
    assert "publication-date<today(" not in q


def test_an_offset_window_is_bounded_on_both_sides():
    """A backfill walks [30,60) as its own retryable job. Without the upper
    bound every step would re-ask for everything newer than itself, and the
    backfill would get slower the further back it went."""
    q = _sharded(ted_open_only=False, ted_window_offset_days=30).build_query()
    assert "publication-date>=today(-60)" in q
    assert "publication-date<today(-30)" in q


@pytest.mark.asyncio
async def test_a_notice_in_several_divisions_is_collapsed_once(monkeypatch):
    """The same publication number carries codes from several divisions.

    The upsert would collapse it anyway -- but only after we had paid to
    embed it. Collapsing here is the difference between the duplicate being
    free and the duplicate being billed.
    """
    from app.agents.ted import TedAgent

    agent = _sharded()

    async def _walk(self, client, query, limit):
        # Every shard finds the same notice, plus one of its own.
        shared = agent.build_stub({
            "publication-number": "111-2026",
            "notice-title": {"eng": "shared across divisions"},
        })
        # Derived from the shard's own CPV code. Taking a slice of the query
        # string looked unique and was not: every query ends with the same
        # SORT clause, so all three shards produced one id.
        division = re.search(r"IN \((\d+)\)", query).group(1)
        own = agent.build_stub({
            "publication-number": f"own-{division}",
            "notice-title": {"eng": "unique"},
        })
        return [s for s in (shared, own) if s], "exhausted", 2

    monkeypatch.setattr(TedAgent, "_walk", _walk)
    stubs = await agent.discover(limit=100)

    ids = [s.external_id for s in stubs]
    assert ids.count("111-2026") == 1, f"duplicate survived: {ids}"
    assert len(stubs) == 4, ids  # 1 shared + 3 unique


@pytest.mark.asyncio
async def test_one_dead_shard_does_not_take_the_sweep_with_it(monkeypatch):
    """Losing a division is a partial harvest. Losing the sweep is an outage.

    The API drops connections under sustained paging, so this is the ordinary
    case rather than the exotic one.
    """
    from app.agents.ted import TedAgent

    agent = _sharded()
    calls = []

    async def _walk(self, client, query, limit):
        calls.append(query)
        if "34000000" in query:
            raise RuntimeError("connection reset by peer")
        stub = agent.build_stub({
            "publication-number": f"n-{len(calls)}",
            "notice-title": {"eng": "ok"},
        })
        return [stub], "exhausted", 1

    monkeypatch.setattr(TedAgent, "_walk", _walk)
    stubs = await agent.discover(limit=100)

    assert len(calls) == 3, "the sweep stopped instead of continuing"
    assert len(stubs) == 2, "the surviving shards' notices were lost"
    assert agent.pagination_truncated is True, \
        "a sweep that lost a division must not report itself as complete"


# --- no division starves because of where it sits in a list ---------------

@pytest.mark.asyncio
async def test_a_big_division_cannot_eat_the_whole_budget(monkeypatch):
    """Measured on a real 20,000-tender sweep before this rule existed:

        33000000 medical       4,889
        45000000 construction  3,887
        ...
        39000000 furniture        29
        60000000 transport          5
        80000000 education          1
        and seven divisions never ran at all.

    Which divisions starve was decided by their position in a config list.
    That is not a procurement judgement.
    """
    from app.agents.ted import TedAgent

    agent = _sharded()                      # three divisions
    budgets = []

    async def _walk(self, client, query, limit):
        budgets.append(limit)
        # Every division could supply far more than the whole budget.
        stubs = [
            agent.build_stub({"publication-number": f"{len(budgets)}-{i}",
                              "notice-title": {"eng": "n"}})
            for i in range(limit + 50)
        ]
        return [s for s in stubs if s][:limit], "limit", 9999

    monkeypatch.setattr(TedAgent, "_walk", _walk)
    stubs = await agent.discover(limit=300)

    assert budgets[0] == 100, f"the first division was handed {budgets[0]} of 300"
    assert len(budgets) == 3, "a later division never got a turn"
    assert len(stubs) == 300


@pytest.mark.asyncio
async def test_an_empty_division_hands_its_share_forward(monkeypatch):
    """The share is recomputed per shard, not fixed up front, so a division
    with nothing to give does not reserve a slice nobody can use."""
    from app.agents.ted import TedAgent

    agent = _sharded()
    budgets = []

    async def _walk(self, client, query, limit):
        budgets.append(limit)
        if "35000000" in query:
            return [], "exhausted", 0       # first division is empty
        stub = agent.build_stub({"publication-number": f"n{len(budgets)}",
                                 "notice-title": {"eng": "n"}})
        return [stub], "exhausted", 1

    monkeypatch.setattr(TedAgent, "_walk", _walk)
    await agent.discover(limit=300)

    assert budgets[0] == 100
    assert budgets[1] == 150, f"the empty division's share was not passed on: {budgets}"


@pytest.mark.asyncio
async def test_the_last_division_gets_the_whole_remainder(monkeypatch):
    """By then there is nobody left to starve."""
    from app.agents.ted import TedAgent

    agent = _sharded()
    budgets = []

    async def _walk(self, client, query, limit):
        budgets.append(limit)
        return [], "exhausted", 0

    monkeypatch.setattr(TedAgent, "_walk", _walk)
    await agent.discover(limit=300)

    assert budgets[-1] == 300, f"the last shard was capped at {budgets[-1]}"
