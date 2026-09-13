"""Email MCP Server - Send and read emails via SMTP/IMAP."""

import email
import json
import os
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

from mcp.server import Server
from mcp.types import Resource, Tool


def create_email_server() -> Server:
    """Create Email MCP server instance."""
    server = Server("email")

    # Configuration from environment
    smtp_host = os.getenv("SMTP_HOST", "")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER", "")
    smtp_password = os.getenv("SMTP_PASSWORD", "")
    imap_host = os.getenv("IMAP_HOST", "")
    imap_port = int(os.getenv("IMAP_PORT", "993"))

    @server.list_resources()
    async def list_resources() -> list[Resource]:
        """List available email resources."""
        return [
            Resource(
                uri="email://inbox",
                name="Inbox",
                mimeType="application/json",
                description="Access inbox messages",
            ),
            Resource(
                uri="email://sent",
                name="Sent Messages",
                mimeType="application/json",
                description="Access sent messages",
            ),
        ]

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        """List available email tools."""
        return [
            Tool(
                name="email_send",
                description="Send an email via SMTP",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "to": {
                            "type": "string",
                            "description": "Recipient email address",
                        },
                        "subject": {
                            "type": "string",
                            "description": "Email subject",
                        },
                        "body": {
                            "type": "string",
                            "description": "Email body (plain text or HTML)",
                        },
                        "cc": {
                            "type": "string",
                            "description": "CC recipients (comma-separated)",
                        },
                        "bcc": {
                            "type": "string",
                            "description": "BCC recipients (comma-separated)",
                        },
                        "html": {
                            "type": "boolean",
                            "description": "Whether body is HTML",
                            "default": False,
                        },
                    },
                    "required": ["to", "subject", "body"],
                },
            ),
            Tool(
                name="email_read_inbox",
                description="Read messages from inbox",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of messages to retrieve",
                            "default": 10,
                        },
                        "unread_only": {
                            "type": "boolean",
                            "description": "Only fetch unread messages",
                            "default": False,
                        },
                    },
                },
            ),
            Tool(
                name="email_search",
                description="Search emails by criteria",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query (subject, from, or keyword)",
                        },
                        "folder": {
                            "type": "string",
                            "description": "Folder to search (INBOX, SENT, etc.)",
                            "default": "INBOX",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum results",
                            "default": 20,
                        },
                    },
                    "required": ["query"],
                },
            ),
            Tool(
                name="email_mark_read",
                description="Mark email as read by message ID",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "message_id": {
                            "type": "string",
                            "description": "Message ID",
                        },
                    },
                    "required": ["message_id"],
                },
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: Any) -> list[Any]:
        """Handle tool execution."""
        try:
            if name == "email_send":
                if not all([smtp_host, smtp_user, smtp_password]):
                    return [
                        {
                            "type": "text",
                            "text": "Error: SMTP credentials not configured. Set SMTP_HOST, SMTP_USER, SMTP_PASSWORD.",
                        }
                    ]

                import smtplib

                msg = MIMEMultipart("alternative")
                msg["From"] = smtp_user
                msg["To"] = arguments["to"]
                msg["Subject"] = arguments["subject"]

                if "cc" in arguments:
                    msg["Cc"] = arguments["cc"]
                if "bcc" in arguments:
                    msg["Bcc"] = arguments["bcc"]

                body_type = "html" if arguments.get("html", False) else "plain"
                msg.attach(MIMEText(arguments["body"], body_type))

                with smtplib.SMTP(smtp_host, smtp_port) as server_conn:
                    server_conn.starttls()
                    server_conn.login(smtp_user, smtp_password)

                    recipients = [arguments["to"]]
                    if "cc" in arguments:
                        recipients.extend(arguments["cc"].split(","))
                    if "bcc" in arguments:
                        recipients.extend(arguments["bcc"].split(","))

                    server_conn.send_message(msg, smtp_user, recipients)

                return [{"type": "text", "text": f"Email sent to {arguments['to']}"}]

            elif name == "email_read_inbox":
                if not all([imap_host, smtp_user, smtp_password]):
                    return [
                        {
                            "type": "text",
                            "text": "Error: IMAP credentials not configured. Set IMAP_HOST, SMTP_USER, SMTP_PASSWORD.",
                        }
                    ]

                import imaplib

                limit = arguments.get("limit", 10)
                unread_only = arguments.get("unread_only", False)

                mail = imaplib.IMAP4_SSL(imap_host, imap_port)
                mail.login(smtp_user, smtp_password)
                mail.select("INBOX")

                search_criteria = "UNSEEN" if unread_only else "ALL"
                _, message_numbers = mail.search(None, search_criteria)

                messages = []
                for num in message_numbers[0].split()[-limit:]:
                    _, msg_data = mail.fetch(num, "(RFC822)")
                    if msg_data and msg_data[0]:
                        email_body = msg_data[0][1]
                        if isinstance(email_body, bytes):
                            email_msg = email.message_from_bytes(email_body)

                            messages.append({
                                "id": num.decode(),
                                "from": email_msg.get("From"),
                                "subject": email_msg.get("Subject"),
                                "date": email_msg.get("Date"),
                            })

                mail.close()
                mail.logout()

                return [
                    {
                        "type": "text",
                        "text": f"Retrieved {len(messages)} messages:\n{json.dumps(messages, indent=2)}",
                    }
                ]

            elif name == "email_search":
                if not all([imap_host, smtp_user, smtp_password]):
                    return [
                        {
                            "type": "text",
                            "text": "Error: IMAP credentials not configured.",
                        }
                    ]

                import imaplib

                query = arguments["query"]
                folder = arguments.get("folder", "INBOX")
                limit = arguments.get("limit", 20)

                mail = imaplib.IMAP4_SSL(imap_host, imap_port)
                mail.login(smtp_user, smtp_password)
                mail.select(folder)

                # Search by subject or from
                _, message_numbers = mail.search(None, f'OR SUBJECT "{query}" FROM "{query}"')

                messages = []
                for num in message_numbers[0].split()[-limit:]:
                    _, msg_data = mail.fetch(num, "(RFC822)")
                    if msg_data and msg_data[0]:
                        email_body = msg_data[0][1]
                        if isinstance(email_body, bytes):
                            email_msg = email.message_from_bytes(email_body)

                            messages.append({
                                "id": num.decode(),
                                "from": email_msg.get("From"),
                                "subject": email_msg.get("Subject"),
                                "date": email_msg.get("Date"),
                            })

                mail.close()
                mail.logout()

                return [
                    {
                        "type": "text",
                        "text": f"Found {len(messages)} matching messages:\n{json.dumps(messages, indent=2)}",
                    }
                ]

            elif name == "email_mark_read":
                if not all([imap_host, smtp_user, smtp_password]):
                    return [
                        {
                            "type": "text",
                            "text": "Error: IMAP credentials not configured.",
                        }
                    ]

                import imaplib

                message_id = arguments["message_id"]

                mail = imaplib.IMAP4_SSL(imap_host, imap_port)
                mail.login(smtp_user, smtp_password)
                mail.select("INBOX")

                mail.store(message_id, "+FLAGS", "\\Seen")

                mail.close()
                mail.logout()

                return [{"type": "text", "text": f"Marked message {message_id} as read"}]

            else:
                return [{"type": "text", "text": f"Unknown tool: {name}"}]

        except Exception as e:
            return [{"type": "text", "text": f"Email error: {str(e)}"}]

    return server
