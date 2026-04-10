from __future__ import annotations

import os
import smtplib
from email import encoders as _encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


def send_email(
    to_address: str | list[str],
    subject: str,
    html_body: str,
    attachments: list[dict] | None = None,
) -> None:
    """
    Send an HTML email via SMTP with STARTTLS.

    to_address: single email string or list of email strings.

    Configure via environment variables in .env:
        SMTP_HOST        – SMTP server host          (default: smtp.gmail.com)
        SMTP_PORT        – SMTP port                 (default: 587)
        SMTP_USER        – login / sender address
        SMTP_PASS        – password / app-password
        SMTP_FROM_EMAIL  – explicit From address     (defaults to SMTP_USER)
        SMTP_FROM_NAME   – display name              (default: SmallShop)

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

    host       = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port       = int(os.environ.get("SMTP_PORT", "587"))
    user       = os.environ.get("SMTP_USER", "")
    password   = os.environ.get("SMTP_PASS", "")
    from_addr  = os.environ.get("SMTP_FROM_EMAIL", "") or user
    from_name  = os.environ.get("SMTP_FROM_NAME", "SmallShop")

    if not from_addr:
        raise RuntimeError(
            "Email is not configured. "
            "Set SMTP_USER (and optionally SMTP_PASS, SMTP_HOST, SMTP_PORT) in your .env file."
        )

    if attachments:
        msg = MIMEMultipart("mixed")
        alt = MIMEMultipart("alternative")
        alt.attach(MIMEText(html_body, "html", "utf-8"))
        msg.attach(alt)
        for att in attachments:
            maintype, subtype = att["content_type"].split("/", 1)
            part = MIMEBase(maintype, subtype)
            part.set_payload(att["data"])
            _encoders.encode_base64(part)
            part.add_header(
                "Content-Disposition", "attachment", filename=att["filename"]
            )
            msg.attach(part)
    else:
        msg = MIMEMultipart("alternative")
        msg.attach(MIMEText(html_body, "html", "utf-8"))

    msg["Subject"] = subject
    msg["From"]    = f"{from_name} <{from_addr}>" if from_name else from_addr
    msg["To"]      = ", ".join(recipients)

    try:
        with smtplib.SMTP(host, port, timeout=20) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            if user and password:
                server.login(user, password)
            server.sendmail(from_addr, recipients, msg.as_string())
    except smtplib.SMTPAuthenticationError as exc:
        raise RuntimeError(
            "SMTP authentication failed – check SMTP_USER / SMTP_PASS in .env"
        ) from exc
    except smtplib.SMTPException as exc:
        raise RuntimeError(f"Failed to send email: {exc}") from exc
    except OSError as exc:
        raise RuntimeError(f"SMTP connection error ({host}:{port}): {exc}") from exc

