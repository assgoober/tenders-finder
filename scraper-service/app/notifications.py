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

    if review or failed:
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
