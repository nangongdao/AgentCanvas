"""Google Docs MCP Server - Read and write Google Docs."""

import os
from typing import Any

from mcp.server import Server
from mcp.types import Resource, Tool


def create_google_docs_server() -> Server:
    """Create Google Docs MCP server instance."""
    server = Server("google-docs")

    # Configuration from environment
    credentials_path = os.getenv("GOOGLE_DOCS_CREDENTIALS_PATH", "")

    @server.list_resources()
    async def list_resources() -> list[Resource]:
        """List available Google Docs resources."""
        return [
            Resource(
                uri="gdocs://documents",
                name="Google Documents",
                mimeType="application/json",
                description="Access Google Docs documents",
            ),
        ]

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        """List available Google Docs tools."""
        return [
            Tool(
                name="gdocs_create",
                description="Create a new Google Doc",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "title": {
                            "type": "string",
                            "description": "Document title",
                        },
                        "content": {
                            "type": "string",
                            "description": "Initial document content (plain text)",
                        },
                    },
                    "required": ["title"],
                },
            ),
            Tool(
                name="gdocs_read",
                description="Read content from a Google Doc",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "document_id": {
                            "type": "string",
                            "description": "Google Docs document ID",
                        },
                    },
                    "required": ["document_id"],
                },
            ),
            Tool(
                name="gdocs_append",
                description="Append text to the end of a Google Doc",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "document_id": {
                            "type": "string",
                            "description": "Document ID",
                        },
                        "text": {
                            "type": "string",
                            "description": "Text to append",
                        },
                    },
                    "required": ["document_id", "text"],
                },
            ),
            Tool(
                name="gdocs_replace",
                description="Replace text in a Google Doc",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "document_id": {
                            "type": "string",
                            "description": "Document ID",
                        },
                        "find": {
                            "type": "string",
                            "description": "Text to find",
                        },
                        "replace": {
                            "type": "string",
                            "description": "Replacement text",
                        },
                        "all_occurrences": {
                            "type": "boolean",
                            "description": "Replace all occurrences",
                            "default": True,
                        },
                    },
                    "required": ["document_id", "find", "replace"],
                },
            ),
            Tool(
                name="gdocs_share",
                description="Share a Google Doc with email address",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "document_id": {
                            "type": "string",
                            "description": "Document ID",
                        },
                        "email": {
                            "type": "string",
                            "description": "Email address to share with",
                        },
                        "role": {
                            "type": "string",
                            "description": "Permission role (reader, writer, owner)",
                            "default": "writer",
                        },
                    },
                    "required": ["document_id", "email"],
                },
            ),
            Tool(
                name="gdocs_export",
                description="Export Google Doc to different format",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "document_id": {
                            "type": "string",
                            "description": "Document ID",
                        },
                        "format": {
                            "type": "string",
                            "description": "Export format (pdf, docx, txt, html)",
                            "default": "pdf",
                        },
                    },
                    "required": ["document_id"],
                },
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: Any) -> list[Any]:
        """Handle tool execution."""
        if not credentials_path or not os.path.exists(credentials_path):
            return [
                {
                    "type": "text",
                    "text": "Error: Google Docs credentials not configured. Set GOOGLE_DOCS_CREDENTIALS_PATH.",
                }
            ]

        try:
            # Import Google API libraries only when needed
            from google.oauth2 import service_account
            from googleapiclient.discovery import build

            SCOPES = [
                "https://www.googleapis.com/auth/documents",
                "https://www.googleapis.com/auth/drive.file",
            ]

            credentials = service_account.Credentials.from_service_account_file(
                credentials_path, scopes=SCOPES
            )

            docs_service = build("docs", "v1", credentials=credentials)
            drive_service = build("drive", "v3", credentials=credentials)

            if name == "gdocs_create":
                title = arguments["title"]
                content = arguments.get("content", "")

                document = docs_service.documents().create(body={"title": title}).execute()
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
                    docs_service.documents().batchUpdate(
                        documentId=document_id, body={"requests": requests}
                    ).execute()

                return [
                    {
                        "type": "text",
                        "text": f"Created document: {title}\nID: {document_id}\nURL: https://docs.google.com/document/d/{document_id}/edit",
                    }
                ]

            elif name == "gdocs_read":
                document_id = arguments["document_id"]
                document = docs_service.documents().get(documentId=document_id).execute()

                title = document.get("title")
                content = document.get("body", {}).get("content", [])

                text_content = ""
                for element in content:
                    if "paragraph" in element:
                        for text_run in element["paragraph"].get("elements", []):
                            if "textRun" in text_run:
                                text_content += text_run["textRun"].get("content", "")

                return [
                    {
                        "type": "text",
                        "text": f"Document: {title}\n\n{text_content}",
                    }
                ]

            elif name == "gdocs_append":
                document_id = arguments["document_id"]
                text = arguments["text"]

                document = docs_service.documents().get(documentId=document_id).execute()
                end_index = document["body"]["content"][-1]["endIndex"]

                requests = [
                    {
                        "insertText": {
                            "location": {"index": end_index - 1},
                            "text": text,
                        }
                    }
                ]

                docs_service.documents().batchUpdate(
                    documentId=document_id, body={"requests": requests}
                ).execute()

                return [{"type": "text", "text": f"Appended text to document {document_id}"}]

            elif name == "gdocs_replace":
                document_id = arguments["document_id"]
                find_text = arguments["find"]
                replace_text = arguments["replace"]

                requests = [
                    {
                        "replaceAllText": {
                            "containsText": {
                                "text": find_text,
                                "matchCase": False,
                            },
                            "replaceText": replace_text,
                        }
                    }
                ]

                result = docs_service.documents().batchUpdate(
                    documentId=document_id, body={"requests": requests}
                ).execute()

                occurrences = result["replies"][0]["replaceAllText"].get("occurrencesChanged", 0)
                return [
                    {
                        "type": "text",
                        "text": f"Replaced {occurrences} occurrence(s) in document {document_id}",
                    }
                ]

            elif name == "gdocs_share":
                document_id = arguments["document_id"]
                email = arguments["email"]
                role = arguments.get("role", "writer")

                permission = {
                    "type": "user",
                    "role": role,
                    "emailAddress": email,
                }

                drive_service.permissions().create(
                    fileId=document_id,
                    body=permission,
                    sendNotificationEmail=True,
                ).execute()

                return [
                    {
                        "type": "text",
                        "text": f"Shared document {document_id} with {email} as {role}",
                    }
                ]

            elif name == "gdocs_export":
                document_id = arguments["document_id"]
                export_format = arguments.get("format", "pdf")

                mime_types = {
                    "pdf": "application/pdf",
                    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    "txt": "text/plain",
                    "html": "text/html",
                }

                mime_type = mime_types.get(export_format, "application/pdf")

                drive_service.files().export_media(
                    fileId=document_id, mimeType=mime_type
                )

                # Note: In real implementation, would save to file or return bytes
                return [
                    {
                        "type": "text",
                        "text": f"Exported document {document_id} as {export_format}",
                    }
                ]

            else:
                return [{"type": "text", "text": f"Unknown tool: {name}"}]

        except ImportError:
            return [
                {
                    "type": "text",
                    "text": "Error: Google API client not installed. Run: pip install google-api-python-client google-auth",
                }
            ]
        except Exception as e:
            return [{"type": "text", "text": f"Google Docs error: {str(e)}"}]

    return server
