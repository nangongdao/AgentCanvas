"""Email MCP server — send/read emails via SMTP/IMAP (stdio transport).

Renamed from ``email.py``: a module named ``email`` shadows the stdlib
``email`` package and breaks ``import email`` / ``email.mime`` when the
server directory sits on ``sys.path``.
"""

from __future__ import annotations

import email as email_stdlib
import json
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

from mcp.server.mcpserver import MCPServer

mcp = MCPServer("email")

_SMTP_HOST = os.getenv("SMTP_HOST", "")
_SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
_SMTP_USER = os.getenv("SMTP_USER", "")
_SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
_IMAP_HOST = os.getenv("IMAP_HOST", "")
_IMAP_PORT = int(os.getenv("IMAP_PORT", "993"))


def _smtp_configured() -> bool:
    return bool(_SMTP_HOST and _SMTP_USER and _SMTP_PASSWORD)


def _imap_configured() -> bool:
    return bool(_IMAP_HOST and _SMTP_USER and _SMTP_PASSWORD)


@mcp.tool()
def email_send(
    to: str,
    subject: str,
    body: str,
    cc: str | None = None,
    bcc: str | None = None,
    html: bool = False,
) -> str:
    """Send an email via SMTP. `to`/`cc`/`bcc` accept comma-separated lists."""
    if not _smtp_configured():
        return "Error: SMTP credentials not configured. Set SMTP_HOST, SMTP_USER, SMTP_PASSWORD."

    msg = MIMEMultipart("alternative")
    msg["From"] = _SMTP_USER
    msg["To"] = to
    msg["Subject"] = subject
    if cc:
        msg["Cc"] = cc
    if bcc:
        msg["Bcc"] = bcc

    msg.attach(MIMEText(body, "html" if html else "plain"))

    recipients = [addr.strip() for addr in [to, cc or "", bcc or ""] if addr.strip()]
    with smtplib.SMTP(_SMTP_HOST, _SMTP_PORT) as conn:
        conn.starttls()
        conn.login(_SMTP_USER, _SMTP_PASSWORD)
        conn.send_message(msg, _SMTP_USER, recipients)

    return f"Email sent to {to}"


def _fetch_messages(
    criteria: str,
    limit: int,
    folder: str = "INBOX",
) -> list[dict[str, str]]:
    """Login to IMAP, fetch matching messages, return lightweight summaries."""
    import imaplib

    messages: list[dict[str, str]] = []
    mail = imaplib.IMAP4_SSL(_IMAP_HOST, _IMAP_PORT)
    try:
        mail.login(_SMTP_USER, _SMTP_PASSWORD)
        mail.select(folder)
        _, nums = mail.search(None, criteria)
        for num in nums[0].split()[-limit:]:
            _, data = mail.fetch(num, "(RFC822)")
            if not data or not data[0]:
                continue
            raw = data[0][1]
            if not isinstance(raw, bytes):
                continue
            eml = email_stdlib.message_from_bytes(raw)
            messages.append(
                {
                    "id": num.decode(),
                    "from": eml.get("From") or "",
                    "subject": eml.get("Subject") or "",
                    "date": eml.get("Date") or "",
                }
            )
    finally:
        try:
            mail.close()
        finally:
            mail.logout()
    return messages


@mcp.tool()
def email_read_inbox(limit: int = 10, unread_only: bool = False) -> str:
    """Read messages from the inbox (latest `limit` messages)."""
    if not _imap_configured():
        return "Error: IMAP credentials not configured. Set IMAP_HOST, SMTP_USER, SMTP_PASSWORD."

    criteria = "UNSEEN" if unread_only else "ALL"
    messages = _fetch_messages(criteria, limit)
    return f"Retrieved {len(messages)} messages:\n{json.dumps(messages, indent=2, ensure_ascii=False)}"


@mcp.tool()
def email_search(query: str, folder: str = "INBOX", limit: int = 20) -> str:
    """Search emails by subject or from in the given IMAP folder."""
    if not _imap_configured():
        return "Error: IMAP credentials not configured."

    # Quote the query to keep SMTP search syntax valid (no injection of
    # parentheses/quotes from user input).
    quoted = query.replace("\\", "\\\\").replace('"', '\\"')
    criteria = f'OR SUBJECT "{quoted}" FROM "{quoted}"'
    messages = _fetch_messages(criteria, limit, folder=folder)
    return f"Found {len(messages)} matching messages:\n{json.dumps(messages, indent=2, ensure_ascii=False)}"


@mcp.tool()
def email_mark_read(message_id: str) -> str:
    """Mark an email as read by message id."""
    if not _imap_configured():
        return "Error: IMAP credentials not configured."

    import imaplib

    mail = imaplib.IMAP4_SSL(_IMAP_HOST, _IMAP_PORT)
    try:
        mail.login(_SMTP_USER, _SMTP_PASSWORD)
        mail.select("INBOX")
        mail.store(message_id, "+FLAGS", "\\Seen")
    finally:
        mail.logout()
    return f"Marked message {message_id} as read"


if __name__ == "__main__":
    mcp.run()