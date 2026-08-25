"""Tests for Israeli-locale parsing.

Weighted toward the failures that produce a plausible-looking wrong value
rather than an error: month/day inversion, timezone drift, and bidi marks
breaking exact matches on reference numbers. Those are the ones no downstream
check can catch.
"""

from __future__ import annotations

from datetime import date, datetime, time

import pytest

from app.agents.hebrew import (
    ISRAEL_TZ,
    clean_text,
    extract_tender_number,
    parse_amount,
    parse_israeli_date,
    parse_israeli_datetime,
    parse_status,
)


# --- Dates -------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("03/04/2026", date(2026, 4, 3)),
        ("3.4.2026", date(2026, 4, 3)),
        ("03-04-2026", date(2026, 4, 3)),
        ("31/12/2026", date(2026, 12, 31)),
        ("1/1/26", date(2026, 1, 1)),
    ],
)
def test_numeric_dates_are_day_first(raw, expected):
    """Israeli convention. Reading 03/04 as 4 March would silently move a
    deadline a month earlier."""
    assert parse_israeli_date(raw) == expected


def test_day_first_is_not_a_guess_when_both_parts_are_ambiguous():
    assert parse_israeli_date("05/06/2026") == date(2026, 6, 5)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("3 באפריל 2026", date(2026, 4, 3)),
        ("15 בינואר 2026", date(2026, 1, 15)),
        ("1 במרץ 2026", date(2026, 3, 1)),
        ("1 במרס 2026", date(2026, 3, 1)),
        ("28 בדצמבר 2026", date(2026, 12, 28)),
    ],
)
def test_hebrew_long_form_dates(raw, expected):
    assert parse_israeli_date(raw) == expected


def test_iso_format_is_recovered_only_when_day_first_is_impossible():
    # 2026-04-03 read day-first gives day=2026, which cannot be a date.
    assert parse_israeli_date("2026-04-03") == date(2026, 4, 3)


def test_impossible_date_returns_none_rather_than_shifting():
    assert parse_israeli_date("32/13/2026") is None


def test_unparseable_input_returns_none():
    for raw in ["", None, "בקרוב", "לא צוין", "N/A"]:
        assert parse_israeli_date(raw) is None


def test_date_is_found_inside_surrounding_prose():
    assert parse_israeli_date('מועד אחרון להגשה: 03/04/2026 בשעה 12:00') == date(2026, 4, 3)


# --- Datetimes and timezone --------------------------------------------------


def test_time_is_attached_as_israel_wall_clock():
    parsed = parse_israeli_datetime("03/04/2026 12:00")
    assert parsed == datetime(2026, 4, 3, 12, 0, tzinfo=ISRAEL_TZ)
    assert parsed.tzinfo is ISRAEL_TZ


def test_deadline_in_dst_resolves_to_plus_three():
    """April is Israel Daylight Time. A fixed +02:00 offset would put this
    submission deadline an hour late."""
    parsed = parse_israeli_datetime("03/04/2026 12:00")
    assert parsed.utcoffset().total_seconds() == 3 * 3600


def test_deadline_in_winter_resolves_to_plus_two():
    parsed = parse_israeli_datetime("15/01/2026 12:00")
    assert parsed.utcoffset().total_seconds() == 2 * 3600


def test_hebrew_time_separator_is_accepted():
    assert parse_israeli_datetime("03/04/2026 בשעה 15.30") == datetime(
        2026, 4, 3, 15, 30, tzinfo=ISRAEL_TZ
    )


def test_missing_time_does_not_silently_become_end_of_day():
    """Assuming 23:59 for a submission deadline is exactly the helpful guess
    that loses a bid. Callers must opt in."""
    assert parse_israeli_datetime("03/04/2026") == datetime(2026, 4, 3, 0, 0, tzinfo=ISRAEL_TZ)

    opted_in = parse_israeli_datetime("03/04/2026", default_time=time(23, 59))
    assert opted_in == datetime(2026, 4, 3, 23, 59, tzinfo=ISRAEL_TZ)


def test_date_digits_are_not_misread_as_a_time():
    assert parse_israeli_datetime("03/04/2026") == datetime(2026, 4, 3, 0, 0, tzinfo=ISRAEL_TZ)


def test_invalid_clock_values_fall_back_to_midnight():
    assert parse_israeli_datetime("03/04/2026 99:99") == datetime(
        2026, 4, 3, 0, 0, tzinfo=ISRAEL_TZ
    )


# --- Bidi and whitespace -----------------------------------------------------


def test_bidi_marks_are_stripped():
    """These are invisible in the browser but present in the scraped string,
    and they break exact matching on reference numbers."""
    assert clean_text("‏מכרז פומבי‎") == "מכרז פומבי"
    assert clean_text("‫טקסט‬") == "טקסט"


def test_nbsp_and_runs_of_whitespace_collapse():
    assert clean_text("מכרז\xa0\xa0פומבי\n\n  2026") == "מכרז פומבי 2026"


def test_clean_text_returns_none_for_blank():
    assert clean_text("   ") is None
    assert clean_text(None) is None


def test_reference_number_survives_bidi_wrapping():
    assert extract_tender_number("‏מכרז פומבי מס' 12/2026‎") == "12/2026"


# --- Status ------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("פתוח להגשה", "open"),
        ("המכרז הסתיים", "closed"),
        ("סגור", "closed"),
        ("בוטל", "cancelled"),
        ("נבחר זוכה", "awarded"),
        ("ימים אחרונים להגשה", "closing_soon"),
    ],
)
def test_status_maps_to_content_agnostic_enum(raw, expected):
    assert parse_status(raw) == expected


def test_cancelled_wins_over_open_when_both_words_appear():
    """Order matters: 'המכרז פורסם ובוטל' must not read as open."""
    assert parse_status("המכרז פורסם ובוטל") == "cancelled"


def test_unknown_status_returns_none():
    assert parse_status("בטיפול") is None
    assert parse_status(None) is None


# --- Amounts -----------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,value,currency",
    [
        ('1,250,000 ש"ח', 1250000.0, "ILS"),
        ("₪ 480,000", 480000.0, "ILS"),
        ("2,000,000 שקלים", 2000000.0, "ILS"),
        ("$1,500,000", 1500000.0, "USD"),
        ("18,500,000 אירו", 18500000.0, "EUR"),
    ],
)
def test_amounts_and_currency(raw, value, currency):
    assert parse_amount(raw) == (value, currency)


def test_thousands_separator_does_not_truncate_the_value():
    """A naive float() on '1,250,000' raises; a naive split on ',' yields 1."""
    amount, _ = parse_amount('1,250,000 ש"ח')
    assert amount == 1_250_000.0


def test_amount_without_currency_still_parses():
    assert parse_amount("750,000") == (750000.0, None)


def test_no_number_returns_none_value():
    assert parse_amount("לא צוין") == (None, None)


# --- Tender reference numbers ------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("מכרז פומבי מס' 12/2026", "12/2026"),
        ("מכרז מס׳ 4-2026", "4-2026"),
        ("מכרז פומבי 2026/17 לאספקת ציוד", "2026/17"),
        ("מספר: 88/2026", "88/2026"),
    ],
)
def test_tender_number_extraction(raw, expected):
    assert extract_tender_number(raw) == expected


def test_first_matching_candidate_wins():
    assert extract_tender_number(None, "אין מספר", "מכרז מס' 7/2026") == "7/2026"


def test_no_reference_returns_none():
    assert extract_tender_number("אספקת שירותי ניקיון") is None
    assert extract_tender_number(None) is None
