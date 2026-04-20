from __future__ import annotations

import base64
import os
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
import json


def send_email(
    to_address: str | list[str],
    subject: str,
    html_body: str,
    attachments: list[dict] | None = None,
    reply_to: str | None = None,
    from_email: str | None = None,
    from_name: str | None = None,
) -> None:
    """
    Send an HTML email via Resend HTTP API.

    to_address: single email string or list of email strings.

    Configure via environment variables in .env:
        RESEND_API_KEY   – Resend API key (re_xxx...)
        RESEND_FROM_EMAIL – From address (e.g. workorders@roobico.com)
        RESEND_FROM_NAME  – display name (default: Roobico)

    attachments: optional list of dicts:
        {"filename": "wo-123.pdf", "data": <bytes>, "content_type": "application/pdf"}

    Raises RuntimeError on configuration error or sending failure.
    """
    if isinstance(to_address, str):
        recipients = [to_address]
    else:
        recipients = list(to_address)
    recipients = [a.strip() for a in recipients if a and a.strip()]
    if not recipients:
        raise RuntimeError("No recipient email addresses provided.")

    api_key = os.environ.get("RESEND_API_KEY", "")
    from_addr = from_email or os.environ.get("RESEND_FROM_EMAIL", "")
    _from_name = from_name or os.environ.get("RESEND_FROM_NAME", "Roobico")

    if not api_key:
        raise RuntimeError(
            "Email is not configured. Set RESEND_API_KEY in your .env file."
        )
    if not from_addr:
        raise RuntimeError(
            "Email is not configured. Set RESEND_FROM_EMAIL in your .env file."
        )

    # Build "from" field
    from_field = f"{_from_name} <{from_addr}>" if _from_name else from_addr

    # Build request payload
    payload: dict = {
        "from": from_field,
        "to": recipients,
        "subject": subject,
        "html": html_body,
    }

    if reply_to:
        payload["reply_to"] = reply_to

    if attachments:
        payload["attachments"] = [
            {
                "filename": att["filename"],
                "content": base64.b64encode(att["data"]).decode("ascii"),
            }
            for att in attachments
        ]

    # Send via Resend API
    req = Request(
        "https://api.resend.com/emails",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "SmallShop-Mailer/1.0",
        },
        method="POST",
    )

    try:
        with urlopen(req, timeout=30) as resp:
            resp.read()  # consume response
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Resend API error ({exc.code}): {body}") from exc
    except URLError as exc:
        raise RuntimeError(f"Failed to connect to Resend API: {exc.reason}") from exc
    except OSError as exc:
        raise RuntimeError(f"Network error sending email: {exc}") from exc

