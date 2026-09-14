"""Parse a raw RFC 822 message (as delivered by the Cloudflare Email Worker)
into the compact shape stored in ``<shop_db>.inbound_emails``.

Only the parts the AI needs are kept: headers, plain text (HTML is
converted to text when there is no text/plain part), and PDF/image
attachments — those are what vendors send confirmations and invoices as.
"""
from __future__ import annotations

import email
import re
from email import policy
from email.message import EmailMessage
from email.utils import getaddresses, parseaddr, parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any

# Attachments we keep bytes for (AI extraction + saved on the order).
EXTRACTABLE_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
    "image/bmp",
    "image/tiff",
}
# The whole raw message must fit Flask's MAX_CONTENT_LENGTH (16 MB) with
# base64 overhead, so keep the kept-attachment budget well below that.
MAX_ATTACHMENT_BYTES = 6 * 1024 * 1024
MAX_TOTAL_ATTACHMENT_BYTES = 8 * 1024 * 1024
MAX_TEXT_CHARS = 60_000


class _HtmlToText(HTMLParser):
    _BLOCK = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "section", "article"}
    _SKIP = {"script", "style", "head", "title"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag in self._BLOCK:
            self._chunks.append("\n")
        elif tag in ("td", "th"):
            self._chunks.append("\t")

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1
        elif tag in self._BLOCK:
            self._chunks.append("\n")

    def handle_data(self, data):
        if not self._skip_depth and data:
            self._chunks.append(data)

    def text(self) -> str:
        raw = "".join(self._chunks)
        raw = re.sub(r"[ \t\r\f\v]+", " ", raw)
        raw = re.sub(r" *\n *", "\n", raw)
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        return raw.strip()


def html_to_text(html: str) -> str:
    if not html:
        return ""
    parser = _HtmlToText()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001 — malformed HTML: fall back to a tag strip
        return re.sub(r"<[^>]+>", " ", html).strip()
    return parser.text()


def _addresses(msg: EmailMessage, header: str) -> list[str]:
    values = msg.get_all(header, [])
    out: list[str] = []
    for _name, addr in getaddresses([str(v) for v in values]):
        addr = (addr or "").strip().lower()
        if addr and addr not in out:
            out.append(addr)
    return out


def _decode_str(value: Any) -> str:
    if value is None:
        return ""
    try:
        return str(value).strip()
    except Exception:  # noqa: BLE001
        return ""


def parse_inbound_mime(raw: bytes) -> dict:
    """Return a dict with: message_id, subject, from_email, from_name, to, cc,
    date (aware datetime or None), text, attachments (list of dicts with
    filename/content_type/size/data), skipped_attachments (meta only),
    header_blob (every routing-relevant header joined, for token lookup)."""
    msg = email.message_from_bytes(raw or b"", policy=policy.default)

    from_name, from_email = parseaddr(_decode_str(msg.get("From")))
    subject = _decode_str(msg.get("Subject"))
    message_id = _decode_str(msg.get("Message-ID") or msg.get("Message-Id")).strip("<> ")

    date_value = None
    try:
        if msg.get("Date"):
            date_value = parsedate_to_datetime(str(msg.get("Date")))
    except Exception:  # noqa: BLE001 — bad Date header: keep None
        date_value = None

    text_parts: list[str] = []
    html_parts: list[str] = []
    attachments: list[dict] = []
    skipped: list[dict] = []
    total_bytes = 0

    for part in msg.walk():
        if part.is_multipart():
            continue
        ctype = (part.get_content_type() or "").lower()
        disposition = (part.get_content_disposition() or "").lower()
        filename = part.get_filename() or ""

        is_attachment = disposition == "attachment" or bool(filename) and ctype not in ("text/plain", "text/html")
        if not is_attachment and ctype == "text/plain":
            try:
                text_parts.append(part.get_content())
            except Exception:  # noqa: BLE001
                payload = part.get_payload(decode=True) or b""
                text_parts.append(payload.decode("utf-8", errors="replace"))
            continue
        if not is_attachment and ctype == "text/html":
            try:
                html_parts.append(part.get_content())
            except Exception:  # noqa: BLE001
                payload = part.get_payload(decode=True) or b""
                html_parts.append(payload.decode("utf-8", errors="replace"))
            continue
        if not is_attachment:
            continue

        try:
            data = part.get_payload(decode=True) or b""
        except Exception:  # noqa: BLE001
            data = b""
        size = len(data)
        meta = {"filename": filename or "attachment", "content_type": ctype, "size": size}
        if ctype not in EXTRACTABLE_TYPES:
            meta["reason"] = "unsupported type"
            skipped.append(meta)
            continue
        if size > MAX_ATTACHMENT_BYTES or total_bytes + size > MAX_TOTAL_ATTACHMENT_BYTES:
            meta["reason"] = "too large"
            skipped.append(meta)
            continue
        total_bytes += size
        attachments.append({**meta, "data": data})

    text = "\n\n".join(t for t in text_parts if t).strip()
    if not text and html_parts:
        text = html_to_text("\n".join(html_parts))
    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS]

    routing_headers = []
    for h in ("To", "Cc", "Bcc", "Delivered-To", "X-Original-To", "X-Forwarded-To",
              "X-Forwarded-For", "Envelope-To", "X-Envelope-To", "Resent-To", "X-Delivered-To"):
        for v in msg.get_all(h, []):
            routing_headers.append(_decode_str(v))

    return {
        "message_id": message_id,
        "subject": subject,
        "from_email": (from_email or "").strip().lower(),
        "from_name": (from_name or "").strip(),
        "to": _addresses(msg, "To"),
        "cc": _addresses(msg, "Cc"),
        "date": date_value,
        "text": text,
        "attachments": attachments,
        "skipped_attachments": skipped,
        "header_blob": " ".join(routing_headers),
    }
