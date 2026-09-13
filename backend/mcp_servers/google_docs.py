"""Google Docs MCP server — read/write Google Docs via the Docs/Drive APIs.

Uses the MCP SDK 2.x ``MCPServer`` API (``@mcp.tool()``) so the file works
with the same SDK version as the other built-in servers.
"""

from __future__ import annotations

import os
from typing import Any

from mcp.server.mcpserver import MCPServer

mcp = MCPServer("google-docs")

_CREDENTIALS_PATH = os.getenv("GOOGLE_DOCS_CREDENTIALS_PATH", "")

_SCOPES = [
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/drive.file",
]


def _clients():
    """Build docs/drive service clients from the configured credentials."""
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    credentials = service_account.Credentials.from_service_account_file(
        _CREDENTIALS_PATH, scopes=_SCOPES
    )
    docs = build("docs", "v1", credentials=credentials)
    drive = build("drive", "v3", credentials=credentials)
    return docs, drive


def _credentials_missing() -> bool:
    return not _CREDENTIALS_PATH or not os.path.exists(_CREDENTIALS_PATH)


@mcp.tool()
def gdocs_create(title: str, content: str = "") -> str:
    """Create a new Google Doc with the given title (and optional content)."""
    if _credentials_missing():
        return "Error: Google Docs credentials not configured. Set GOOGLE_DOCS_CREDENTIALS_PATH."

    try:
        docs, _ = _clients()
        document = docs.documents().create(body={"title": title}).execute()
        document_id = document["documentId"]
        if content:
            requests = [
                {
                    "insertText": {
                        "location": {"index": 1},
                        "text": content,
                    }
                }
            ]
            docs.documents().batchUpdate(
                documentId=document_id, body={"requests": requests}
            ).execute()
        return (
            f"Created document: {title}\n"
            f"ID: {document_id}\n"
            f"URL: https://docs.google.com/document/d/{document_id}/edit"
        )
    except ImportError:
        return "Error: Google API client not installed. Run: pip install google-api-python-client google-auth"
    except Exception as e:
        return f"Google Docs error: {str(e)}"


@mcp.tool()
def gdocs_read(document_id: str) -> str:
    """Read the plain-text content of a Google Doc by id."""
    if _credentials_missing():
        return "Error: Google Docs credentials not configured. Set GOOGLE_DOCS_CREDENTIALS_PATH."

    try:
        docs, _ = _clients()
        document = docs.documents().get(documentId=document_id).execute()
        title = document.get("title", "")
        text_parts: list[str] = []
        for element in document.get("body", {}).get("content", []):
            for text_run in element.get("paragraph", {}).get("elements", []):
                if "textRun" in text_run:
                    text_parts.append(text_run["textRun"].get("content", ""))
        return f"Document: {title}\n\n{''.join(text_parts)}"
    except ImportError:
        return "Error: Google API client not installed. Run: pip install google-api-python-client google-auth"
    except Exception as e:
        return f"Google Docs error: {str(e)}"


@mcp.tool()
def gdocs_append(document_id: str, text: str) -> str:
    """Append text to the end of a Google Doc."""
    if _credentials_missing():
        return "Error: Google Docs credentials not configured. Set GOOGLE_DOCS_CREDENTIALS_PATH."

    try:
        docs, _ = _clients()
        document = docs.documents().get(documentId=document_id).execute()
        end_index = document["body"]["content"][-1]["endIndex"]
        requests = [
            {
                "insertText": {
                    "location": {"index": end_index - 1},
                    "text": text,
                }
            }
        ]
        docs.documents().batchUpdate(
            documentId=document_id, body={"requests": requests}
        ).execute()
        return f"Appended text to document {document_id}"
    except ImportError:
        return "Error: Google API client not installed. Run: pip install google-api-python-client google-auth"
    except Exception as e:
        return f"Google Docs error: {str(e)}"


@mcp.tool()
def gdocs_replace(
    document_id: str,
    find: str,
    replace: str,
    all_occurrences: bool = True,
) -> str:
    """Replace text in a Google Doc ('find' -> 'replace')."""
    if _credentials_missing():
        return "Error: Google Docs credentials not configured. Set GOOGLE_DOCS_CREDENTIALS_PATH."

    try:
        docs, _ = _clients()
        requests = [
            {
                "replaceAllText": {
                    "containsText": {"text": find, "matchCase": False},
                    "replaceText": replace,
                }
            }
        ]
        result = docs.documents().batchUpdate(
            documentId=document_id, body={"requests": requests}
        ).execute()
        occurrences = result["replies"][0]["replaceAllText"].get("occurrencesChanged", 0)
        return f"Replaced {occurrences} occurrence(s) in document {document_id}"
    except ImportError:
        return "Error: Google API client not installed. Run: pip install google-api-python-client google-auth"
    except Exception as e:
        return f"Google Docs error: {str(e)}"


@mcp.tool()
def gdocs_share(document_id: str, email: str, role: str = "writer") -> str:
    """Share a Google Doc with an email address (role: reader/writer/owner)."""
    if _credentials_missing():
        return "Error: Google Docs credentials not configured. Set GOOGLE_DOCS_CREDENTIALS_PATH."

    try:
        _, drive = _clients()
        permission = {"type": "user", "role": role, "emailAddress": email}
        drive.permissions().create(
            fileId=document_id,
            body=permission,
            sendNotificationEmail=True,
        ).execute()
        return f"Shared document {document_id} with {email} as {role}"
    except ImportError:
        return "Error: Google API client not installed. Run: pip install google-api-python-client google-auth"
    except Exception as e:
        return f"Google Docs error: {str(e)}"


@mcp.tool()
def gdocs_export(document_id: str, format: str = "pdf") -> str:
    """Export a Google Doc to pdf/docx/txt/html."""
    if _credentials_missing():
        return "Error: Google Docs credentials not configured. Set GOOGLE_DOCS_CREDENTIALS_PATH."

    try:
        _, drive = _clients()
        mime_types = {
            "pdf": "application/pdf",
            "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "txt": "text/plain",
            "html": "text/html",
        }
        mime_type = mime_types.get(format, "application/pdf")
        drive.files().export_media(fileId=document_id, mimeType=mime_type)
        return f"Exported document {document_id} as {format}"
    except ImportError:
        return "Error: Google API client not installed. Run: pip install google-api-python-client google-auth"
    except Exception as e:
        return f"Google Docs error: {str(e)}"


if __name__ == "__main__":
    mcp.run()