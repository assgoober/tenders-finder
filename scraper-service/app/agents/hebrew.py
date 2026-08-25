"""Hebrew and Israeli-locale parsing helpers.

Israeli procurement portals differ from the generic case in ways that silently
corrupt data rather than raising errors, which is why this is a separate,
heavily tested module rather than inline helpers:

  * **Dates are DD/MM/YYYY.** Read as US MM/DD, `03/04/2026` becomes 4 March
    instead of 3 April — a month-early deadline that looks perfectly valid.
    Nothing downstream can detect that.
  * **Deadlines are wall-clock Israel time**, and Israel observes DST. A naive
    timestamp stored as UTC is off by two or three hours, which matters for a
    submission that closes at 12:00.
  * **RTL control characters** (U+200E/U+200F/U+202A-E) are invisible in
    rendered HTML but land inside scraped strings, breaking exact matching on
    tender reference numbers.

Every function returns None rather than guessing. A null propagates to the
extraction layer as "not stated"; a wrong value propagates as fact.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

# Israel observes DST, so a fixed +02:00/+03:00 offset is wrong for half the
# year. Always attach the named zone and let it resolve.
ISRAEL_TZ = ZoneInfo("Asia/Jerusalem")

# Bidi control characters. Invisible on screen, present in the string.
_BIDI_MARKS = dict.fromkeys(
    [0x200E, 0x200F, 0x202A, 0x202B, 0x202C, 0x202D, 0x202E, 0x2066, 0x2067, 0x2068, 0x2069]
)

_HEBREW_MONTHS = {
    "ינואר": 1,
    "פברואר": 2,
    "מרץ": 3,
    "מרס": 3,  # both spellings are in active use
    "אפריל": 4,
    "מאי": 5,
    "יוני": 6,
    "יולי": 7,
    "אוגוסט": 8,
    "ספטמבר": 9,
    "אוקטובר": 10,
    "נובמבר": 11,
    "דצמבר": 12,
}

# Portal status vocabulary. Mapped to the content-agnostic `tender_status`
# enum, so an Israeli portal needs no enum values of its own.
_STATUS_KEYWORDS: list[tuple[tuple[str, ...], str]] = [
    (("בוטל", "מבוטל", "ביטול מכרז"), "cancelled"),
    (("נבחר זוכה", "הודעת זכייה", "זוכה", "הוכרז זוכה"), "awarded"),
    (("סגור", "הסתיים", "נסגר", "פג תוקף", "המכרז הסתיים"), "closed"),
    (("עומד להיסגר", "ימים אחרונים"), "closing_soon"),
    (("פתוח", "פעיל", "פורסם", "בתוקף"), "open"),
]

_CURRENCY_TOKENS = [
    (("₪", 'ש"ח', "שח", "שקל", "שקלים", "ils", "nis"), "ILS"),
    (("$", "דולר", "usd"), "USD"),
    (("€", "אירו", "יורו", "eur"), "EUR"),
]

_DATE_SEPARATORS = r"[./\-]"
_NUMERIC_DATE = re.compile(
    rf"(?P<first>\d{{1,2}}){_DATE_SEPARATORS}(?P<second>\d{{1,2}}){_DATE_SEPARATORS}(?P<year>\d{{2,4}})"
)
_HEBREW_DATE = re.compile(
    r"(?P<day>\d{1,2})\s+(?:ב)?(?P<month>[א-ת]+)\s*,?\s*(?P<year>\d{4})"
)
# A leading four-digit year is unambiguous, so ISO is matched before the
# day-first read rather than as its fallback. Tried the other way round,
# "2026-04-03" matches the day-first pattern from its third character and
# yields 26 April 2003 — a valid date, so nothing downstream would flag it.
_ISO_DATE = re.compile(
    rf"\b(?P<year>\d{{4}}){_DATE_SEPARATORS}(?P<month>\d{{1,2}}){_DATE_SEPARATORS}(?P<day>\d{{1,2}})\b"
)
_TIME = re.compile(r"(?P<hour>\d{1,2})[:.](?P<minute>\d{2})")


def clean_text(value: str | None) -> str | None:
    """Strip bidi marks, normalize whitespace and Unicode form.

    NFKC folds presentation-form variants that some portals emit for Hebrew
    punctuation, so two strings that render identically compare equal.
    """
    if value is None:
        return None

    cleaned = value.translate(_BIDI_MARKS)
    cleaned = unicodedata.normalize("NFKC", cleaned)
    cleaned = cleaned.replace("\xa0", " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or None


def parse_israeli_date(value: str | None) -> date | None:
    """Parse a date written in Israeli convention.

    Handles `DD/MM/YYYY`, `DD.MM.YYYY`, `DD-MM-YYYY`, two-digit years, and
    Hebrew long form (`3 באפריל 2026`).

    Day-first is assumed, because that is the Israeli convention and these
    portals are Israeli. Where the first component exceeds 12 the reading is
    unambiguous anyway; where both are 12 or under, day-first is the correct
    interpretation for this locale — it is a deliberate locale assumption,
    not a heuristic.
    """
    text = clean_text(value)
    if not text:
        return None

    hebrew = _HEBREW_DATE.search(text)
    if hebrew:
        month = _HEBREW_MONTHS.get(hebrew.group("month"))
        if month:
            return _safe_date(int(hebrew.group("year")), month, int(hebrew.group("day")))

    iso = _ISO_DATE.search(text)
    if iso:
        return _safe_date(int(iso.group("year")), int(iso.group("month")), int(iso.group("day")))

    numeric = _NUMERIC_DATE.search(text)
    if numeric:
        return _safe_date(
            _expand_year(int(numeric.group("year")), len(numeric.group("year"))),
            int(numeric.group("second")),
            int(numeric.group("first")),
        )

    return None


def parse_israeli_datetime(value: str | None, *, default_time: time | None = None) -> datetime | None:
    """Parse a date, plus a time when one is present, as Israel wall-clock.

    Returns a timezone-aware datetime in Asia/Jerusalem.

    When the source states no time, `default_time` decides. Leave it None to
    get midnight — and note that assuming 23:59 for a submission deadline is
    exactly the kind of helpful guess that loses a bid, so callers must opt in
    explicitly rather than inherit it.
    """
    parsed_date = parse_israeli_date(value)
    if parsed_date is None:
        return None

    text = clean_text(value) or ""
    # Look for the clock only after the date, so the date's own digits cannot
    # be read as a time ("03/04/2026" would otherwise yield 03:04).
    tail = text
    for pattern in (_HEBREW_DATE, _ISO_DATE, _NUMERIC_DATE):
        match = pattern.search(text)
        if match:
            tail = text[match.end() :]
            break

    clock = _TIME.search(tail)
    if clock:
        hour, minute = int(clock.group("hour")), int(clock.group("minute"))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return datetime.combine(parsed_date, time(hour, minute), tzinfo=ISRAEL_TZ)

    return datetime.combine(parsed_date, default_time or time(0, 0), tzinfo=ISRAEL_TZ)


def parse_status(value: str | None) -> str | None:
    """Map portal status wording onto the `tender_status` enum."""
    text = clean_text(value)
    if not text:
        return None

    for keywords, status in _STATUS_KEYWORDS:
        if any(keyword in text for keyword in keywords):
            return status
    return None


def parse_amount(value: str | None) -> tuple[float | None, str | None]:
    """Extract a numeric amount and ISO 4217 currency code.

    Israeli portals write `1,250,000 ש"ח`. The thousands separator has to go
    before float(), and the currency has to survive as a code rather than a
    glyph so it round-trips through the database.
    """
    text = clean_text(value)
    if not text:
        return None, None

    currency = None
    lowered = text.lower()
    for tokens, code in _CURRENCY_TOKENS:
        if any(token in lowered for token in tokens):
            currency = code
            break

    match = re.search(r"\d[\d,]*(?:\.\d+)?", text)
    if not match:
        return None, currency

    try:
        return float(match.group(0).replace(",", "")), currency
    except ValueError:
        return None, currency


def extract_tender_number(*candidates: str | None) -> str | None:
    """Pull the buyer's own tender reference out of a title or field.

    Israeli references look like `12/2026`, `מכרז פומבי 4-2026`, or
    `מכרז מס' 2026/17`. This is the dedup key and the number a BD team
    quotes on the phone, so it is preserved verbatim once found rather than
    normalized into a canonical form.
    """
    patterns = [
        re.compile(r"מכרז\s*(?:פומבי\s*)?(?:מס['׳]?\s*)?[:.]?\s*(?P<ref>[\w֐-׿]+[/\-]\d{2,4})"),
        re.compile(r"(?:מס['׳]|מספר)\s*[:.]?\s*(?P<ref>\d+[/\-]\d{2,4})"),
        re.compile(r"\b(?P<ref>\d{1,4}[/\-]\d{4})\b"),
    ]

    for candidate in candidates:
        text = clean_text(candidate)
        if not text:
            continue
        for pattern in patterns:
            match = pattern.search(text)
            if match:
                return match.group("ref")

    return None


def _expand_year(year: int, digits: int) -> int:
    """Expand a two-digit year. Tenders are current, so the window is recent."""
    if digits >= 4:
        return year
    return 2000 + year if year < 70 else 1900 + year


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None
