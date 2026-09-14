"""Run digests over email.

Why this exists: a tender routed to `needs_review` is deliberately left
unembedded, so it generates no alerts and appears nowhere in the dashboard's
feed. Until the review queue is built, nothing in the product says a tender
is waiting for a human. A run that quietly parks a third of its tenders looks
exactly like a run that worked. This is the thing that says otherwise.

Two deliberate limits:

* **Digest per run, not a mail per tender.** A portal sweep touches hundreds
  of rows; per-tender mail would be filtered as noise within a week, which
  is the same as not sending it.
* **Silent on a clean run** unless `NOTIFY_ON_SUCCESS` is set. A message that
  always arrives stops being read, and this one exists to be noticed.

Unconfigured SMTP is not an error. The digest is logged at WARNING and the
run continues — mail is an operational convenience, and a pipeline that
aborts because a mail server is unreachable has traded a real job for a
notification about it.
"""

from __future__ import annotations

import asyncio
import logging
import smtplib
from datetime import datetime
from email.message import EmailMessage
from typing import TYPE_CHECKING, Protocol

from .config import Settings, get_settings

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .pipeline import IngestReport

logger = logging.getLogger(__name__)

#: Errors are truncated in the body. The digest is a prompt to go and look,
#: not the log itself.
MAX_ERRORS_PER_SOURCE = 5


class Notifier(Protocol):
    """So tests and dry runs can substitute a recorder for real mail."""

    async def send_run_digest(self, reports: list[IngestReport]) -> bool: ...


def needs_attention(reports: list[IngestReport]) -> bool:
    return any(r.needs_review or r.failed or r.errors for r in reports)


def build_digest(reports: list[IngestReport]) -> tuple[str, str]:
    """Render (subject, body). Pure — the tests read this, not a mailbox."""
    review = sum(r.needs_review for r in reports)
    refused = sum(r.refused for r in reports)
    failed = sum(r.failed for r in reports)
    ingested = sum(r.ingested for r in reports)
    found = sum(r.found for r in reports)

    partial = [r.source_key for r in reports if getattr(r, "partial", False)]

    if partial:
        # Said first and said plainly. "60 ingested, clean" was true of every
        # row that arrived and false about the run, which is the worst kind of
        # subject line: it is not wrong enough to notice.
        headline = f"PARTIAL harvest ({', '.join(partial)}) — {ingested} ingested"
    elif review or failed:
        headline = f"{review} for review, {failed} failed"
    else:
        headline = f"{ingested} ingested, clean"
    subject = f"[Tenders Finder] {headline}"

    policy_note = f"  (of these, {refused} declined by policy)" if refused else ""
    lines = [
        f"Found {found} tenders across {len(reports)} source(s).",
        f"  ingested     {ingested}",
        f"  needs review {review}{policy_note}",
        f"  failed       {failed}",
        "",
    ]

    if review:
        lines += [
            "Tenders in review are stored but NOT embedded, so they raise no",
            "alerts and will not surface in matching until a human clears them.",
            "",
        ]

    for report in reports:
        lines.append(
            f"[{report.source_key}] found={report.found} ingested={report.ingested} "
            f"review={report.needs_review} refused={report.refused} failed={report.failed}"
        )
        for error in report.errors[:MAX_ERRORS_PER_SOURCE]:
            lines.append(f"    - {error}")
        remaining = len(report.errors) - MAX_ERRORS_PER_SOURCE
        if remaining > 0:
            lines.append(f"    ... and {remaining} more (see the service log)")

    return subject, "\n".join(lines)


def explain_smtp_failure(exc: Exception) -> str:
    """Turn an SMTP failure into the thing to actually go and change.

    Lives here rather than in the diagnostic tool so the pipeline's own error
    log carries the same advice — the run log is where someone looks first
    when a digest stops arriving.
    """
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return (
            "The server rejected the credentials. On Gmail this is almost always the "
            "account password being used instead of an App Password (Google Account → "
            "Security → App passwords, after enabling 2-Step Verification)."
        )
    if isinstance(exc, smtplib.SMTPSenderRefused):
        return (
            "The server refused the From address. Most providers only allow a From they "
            "own — set NOTIFY_FROM_EMAIL to the SMTP_USERNAME address."
        )
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return "The server refused the recipient. Check NOTIFY_EMAIL for a typo."
    if isinstance(exc, smtplib.SMTPNotSupportedError):
        return (
            "The server does not support STARTTLS on this port. Try SMTP_USE_TLS=false, "
            "or port 465."
        )
    if isinstance(exc, (TimeoutError, OSError)):
        return (
            "Could not reach the server. Check SMTP_HOST and SMTP_PORT, and whether "
            "outbound SMTP is blocked from this machine — many networks block port 587."
        )
    return "Unexpected failure; the exception itself is the detail."


# --- Customer match alerts --------------------------------------------------
#
# The run digest above goes to whoever operates the service: how many tenders
# were harvested, how many are waiting for a human. This goes to the customer,
# and it is the only part of the product they ever receive rather than visit.


def group_alerts_by_recipient(rows: list[dict]) -> dict[tuple[str, str], list[dict]]:
    """One message per recipient, not one per match.

    `pending_match_alerts` returns a row per (match, recipient) because the
    grouping is a presentation decision and the database should not be making
    it. Sending each row would mean a customer with seven matches gets seven
    emails on the same morning, which is how an alert becomes a filter rule.
    """
    grouped: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        recipient = (row.get("recipient") or "").strip()
        if not recipient:
            continue
        grouped.setdefault((recipient, row.get("organization") or ""), []).append(row)
    return grouped


def _format_deadline(value: str | None) -> str:
    """Days remaining, because that is the number a bid team acts on.

    An ISO timestamp makes a reader do arithmetic to find out whether they
    have a week or an afternoon.
    """
    if not value:
        return "no deadline published"

    text = str(value).replace("Z", "+00:00")
    try:
        when = datetime.fromisoformat(text)
    except ValueError:
        return str(value)

    now = datetime.now(when.tzinfo) if when.tzinfo else datetime.now()
    days = (when - now).days
    stamp = when.strftime("%d %b %Y")

    if days < 0:
        return f"{stamp} (closed)"
    if days == 0:
        return f"{stamp} (closes today)"
    if days == 1:
        return f"{stamp} (1 day left)"
    return f"{stamp} ({days} days left)"


def build_match_alert(organization: str, matches: list[dict]) -> tuple[str, str]:
    """Render (subject, body) for one recipient. Pure; the tests read this."""
    count = len(matches)
    # Already ordered by deadline in the SQL; relied on rather than re-sorted
    # so the email and the dashboard cannot disagree about what is urgent.
    soonest = _format_deadline(matches[0].get("deadline")) if matches else ""

    noun = "tender" if count == 1 else "tenders"
    subject = f"[Tenders Finder] {count} new {noun} matched {organization}"

    lines = [
        f"{count} new {noun} matched your capability profile.",
        "",
    ]

    for match in matches:
        title = (match.get("title") or "").strip() or match.get("external_id") or "Untitled"
        # Marked, not sorted to the top: they are already first, and a reader
        # scanning twenty titles should not have to read every date to find
        # the ones that will not wait. `lead_days` comes from the query rather
        # than being recomputed here, so the mark and the ordering cannot
        # disagree.
        lead = match.get("lead_days")
        if isinstance(lead, int) and lead <= 7:
            title = f"[URGENT] {title}"
        lines.append(title)
        detail = [f"  deadline: {_format_deadline(match.get('deadline'))}"]
        if match.get("buyer_name"):
            detail.append(f"  buyer:    {match['buyer_name']}")
        detail.append(f"  profile:  {match.get('profile_name', '-')}")
        # The score is shown, not hidden behind a label like "strong match".
        # A BD lead who can see the number can calibrate their own trust in it
        # after a fortnight; one who cannot has to take it or leave it.
        if match.get("score") is not None:
            detail.append(f"  score:    {float(match['score']):.2f}")
        if match.get("source_url"):
            detail.append(f"  source:   {match['source_url']}")
        lines.extend(detail)
        lines.append("")

    lines.append(
        "Every tender above links to its original notice. Nothing here is a "
        "summary you have to trust -- open the source and check."
    )
    if soonest:
        lines.append(f"Soonest deadline in this batch: {soonest}.")

    return subject, "\n".join(lines)


class EmailNotifier:
    """Sends the digest over SMTP."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    async def send_run_digest(self, reports: list[IngestReport]) -> bool:
        """Returns whether a message was actually sent."""
        if not reports:
            return False

        attention = needs_attention(reports)
        if not attention and not self.settings.notify_on_success:
            return False

        subject, body = build_digest(reports)

        if not self.settings.has_email_notifications:
            # Deliberately not silent: without this the only record that a
            # tender is waiting would be a debug line nobody reads.
            logger.warning(
                "email notifications are not configured (set SMTP_HOST and NOTIFY_EMAIL); "
                "digest follows\n%s\n%s",
                subject,
                body,
            )
            return False

        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = self.settings.mail_from
        message["To"] = self.settings.notify_email
        message.set_content(body)

        try:
            # smtplib is blocking; the pipeline runs on the event loop.
            await asyncio.to_thread(self._send, message)
        except Exception as exc:  # noqa: BLE001 - never abort a run over mail
            # The digest goes into the log verbatim: if it could not be
            # delivered, this is the only remaining record that tenders are
            # waiting. The diagnosis goes with it so nobody has to guess.
            logger.error(
                "failed to send the run digest to %s: %s: %s\n%s\nundelivered digest:\n%s",
                self.settings.notify_email,
                type(exc).__name__,
                exc,
                explain_smtp_failure(exc),
                body,
            )
            return False

        logger.info("run digest sent to %s (%s)", self.settings.notify_email, subject)
        return True

    async def send_match_alert(self, recipient: str, subject: str, body: str) -> bool:
        """One customer-facing alert. Returns whether it actually went out.

        The caller only stamps `alerted_at` on a True, so a send that could
        not happen leaves the match pending rather than silently consumed --
        which is the difference between a customer getting their alert late
        and never getting it at all.
        """
        if not self.settings.has_email_notifications:
            logger.warning(
                "email is not configured (set SMTP_HOST); alert for %s not sent\n%s\n%s",
                recipient, subject, body,
            )
            return False

        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = self.settings.mail_from
        message["To"] = recipient
        message.set_content(body)

        try:
            await asyncio.to_thread(self._send, message)
        except Exception as exc:  # noqa: BLE001 - a send must never abort a run
            logger.error(
                "failed to send a match alert to %s: %s: %s\n%s",
                recipient, type(exc).__name__, exc, explain_smtp_failure(exc),
            )
            return False

        logger.info("match alert sent to %s (%s)", recipient, subject)
        return True

    def _send(self, message: EmailMessage) -> None:
        settings = self.settings
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
            if settings.smtp_use_tls:
                smtp.starttls()
            if settings.smtp_username and settings.smtp_password:
                smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.send_message(message)


def get_notifier(settings: Settings | None = None) -> EmailNotifier:
    return EmailNotifier(settings)
