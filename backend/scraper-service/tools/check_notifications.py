"""Verify that run digests can actually be delivered.

The digest only matters if it arrives. Without this, the first time anyone
learns that SMTP is misconfigured is when a run parks thirty tenders and
nobody hears about it — the exact failure the digest exists to prevent.

    python tools/check_notifications.py            # report config, send nothing
    python tools/check_notifications.py --send     # actually send a test digest

Reads the same `.env` the service reads, so a pass here means the service
will deliver too.

## Setting it up with Gmail

Gmail rejects your account password over SMTP. You need an **App Password**:

    1. Google Account -> Security -> turn on 2-Step Verification (required;
       the App Password option does not appear without it).
    2. Google Account -> Security -> App passwords -> generate one.
    3. Put the 16 characters in SMTP_PASSWORD, with no spaces.

    SMTP_HOST=smtp.gmail.com
    SMTP_PORT=587
    SMTP_USERNAME=you@gmail.com
    SMTP_PASSWORD=<the app password>
    NOTIFY_EMAIL=you@gmail.com

Never paste that password into a chat or a commit — it belongs in `.env`,
which is gitignored, and it grants access to the account that owns it.
"""

from __future__ import annotations

import argparse
import asyncio
import smtplib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.notifications import (  # noqa: E402
    EmailNotifier,
    build_digest,
    explain_smtp_failure,
)
from app.pipeline import IngestReport  # noqa: E402

# Shaped like a run that needs attention, so --send exercises the same code
# path a real digest takes rather than a simplified one.
SAMPLE = [
    IngestReport(
        source_key="mod-balam",
        found=23,
        ingested=19,
        needs_review=3,
        refused=1,
        failed=1,
        errors=["1000957926: deadline column was empty"],
    )
]


def describe(settings) -> bool:
    """Print the delivery config. Returns whether it looks complete."""
    print("=== notification config ===")
    print(f"  NOTIFY_EMAIL      {settings.notify_email or '(unset)'}")
    print(f"  From              {settings.mail_from or '(unset)'}")
    print(f"  SMTP_HOST         {settings.smtp_host or '(unset)'}")
    print(f"  SMTP_PORT         {settings.smtp_port}")
    print(f"  SMTP_USERNAME     {settings.smtp_username or '(unset)'}")
    # Never print the password, not even masked by length — the length of a
    # secret is itself a hint.
    print(f"  SMTP_PASSWORD     {'set' if settings.smtp_password else '(unset)'}")
    print(f"  SMTP_USE_TLS      {settings.smtp_use_tls}")
    print(f"  NOTIFY_ON_SUCCESS {settings.notify_on_success}")

    problems = []
    if not settings.notify_email:
        problems.append("NOTIFY_EMAIL is unset — there is nobody to send to.")
    if not settings.smtp_host:
        problems.append(
            "SMTP_HOST is unset — digests will only be logged, never delivered."
        )
    if settings.smtp_username and not settings.smtp_password:
        problems.append("SMTP_USERNAME is set but SMTP_PASSWORD is not; login will fail.")
    if settings.smtp_host == "smtp.gmail.com" and not settings.smtp_use_tls:
        problems.append("Gmail requires STARTTLS on port 587; set SMTP_USE_TLS=true.")

    print()
    if problems:
        print("Not ready to deliver:")
        for problem in problems:
            print(f"  - {problem}")
        return False

    print("Config looks complete. Re-run with --send to prove it end to end.")
    return True


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--send",
        action="store_true",
        help="Actually send a test digest to NOTIFY_EMAIL.",
    )
    args = parser.parse_args()

    settings = get_settings()
    ready = describe(settings)

    subject, body = build_digest(SAMPLE)
    print("\n=== the digest that would be sent ===")
    print(subject)
    print()
    print(body)

    if not args.send:
        print("\n(nothing sent — pass --send to deliver it)")
        return 0 if ready else 1

    if not ready:
        print("\nRefusing to send with an incomplete config; fix the items above first.")
        return 1

    print(f"\nSending to {settings.notify_email} ...")
    notifier = EmailNotifier(settings)
    try:
        # _send rather than send_run_digest: the digest layer deliberately
        # swallows failures so a run is never aborted by mail, and swallowing
        # them is exactly wrong for a diagnostic.
        message = _build_message(settings, subject, body)
        await asyncio.to_thread(notifier._send, message)
    except Exception as exc:  # noqa: BLE001 - this tool exists to report them
        print(f"\nFAILED: {type(exc).__name__}: {exc}")
        print(f"\n{explain_smtp_failure(exc)}")
        return 2

    print("Sent. If it does not arrive within a minute, check the spam folder —")
    print("a first message from a new sender to yourself often lands there.")
    return 0


def _build_message(settings, subject: str, body: str):
    from email.message import EmailMessage

    message = EmailMessage()
    message["Subject"] = f"{subject} [test]"
    message["From"] = settings.mail_from
    message["To"] = settings.notify_email
    message.set_content(
        "This is a test digest from tools/check_notifications.py.\n"
        "Real digests are sent after an ingestion run.\n\n" + body
    )
    return message


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
