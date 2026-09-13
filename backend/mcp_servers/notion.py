"""Notion MCP server: pages, databases, and content operations."""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
from mcp.server.mcpserver import MCPServer

mcp = MCPServer("notion")


def _get_token() -> str:
    """Get Notion token from environment."""
    token = os.environ.get("NOTION_TOKEN", "")
    if not token:
        raise ValueError("NOTION_TOKEN environment variable not set")
    return token


def _make_request(
    method: str,
    endpoint: str,
    json_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Make Notion API request."""
    token = _get_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    }
    url = f"https://api.notion.com/v1{endpoint}"

    try:
        with httpx.Client(timeout=30.0) as client:
            if method == "GET":
                response = client.get(url, headers=headers)
            elif method == "POST":
                response = client.post(url, headers=headers, json=json_data or {})
            elif method == "PATCH":
                response = client.patch(url, headers=headers, json=json_data or {})
            elif method == "DELETE":
                response = client.delete(url, headers=headers)
            else:
                return {"success": False, "error": f"Unsupported method: {method}"}

            response.raise_for_status()
            return {"success": True, "data": response.json()}
    except httpx.HTTPStatusError as e:
        return {
            "success": False,
            "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}",
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
def notion_search(query: str, max_results: int = 10) -> str:
    """Search for pages and databases.

    Args:
        query: Search query text
        max_results: Maximum results (default 10, max 100)

    Returns:
        JSON array of matching pages and databases
    """
    data = {
        "query": query,
        "page_size": min(max(1, max_results), 100),
    }

    result = _make_request("POST", "/search", json_data=data)
    if not result["success"]:
        return json.dumps({"error": result["error"]})

    items = [
        {
            "id": item["id"],
            "object": item["object"],
            "title": _extract_title(item),
            "url": item.get("url", ""),
            "created_time": item.get("created_time"),
            "last_edited_time": item.get("last_edited_time"),
        }
        for item in result["data"].get("results", [])
    ]
    return json.dumps(items, indent=2)


def _extract_title(item: dict[str, Any]) -> str:
    """Extract title from page or database."""
    if item["object"] == "page":
        props = item.get("properties", {})
        title_prop = props.get("title", {})
        if title_prop.get("type") == "title":
            title_arr = title_prop.get("title", [])
            if title_arr:
                return title_arr[0].get("plain_text", "Untitled")
    elif item["object"] == "database":
        title_arr = item.get("title", [])
        if title_arr:
            return title_arr[0].get("plain_text", "Untitled")
    return "Untitled"


@mcp.tool()
def notion_get_page(page_id: str) -> str:
    """Get page details.

    Args:
        page_id: Page ID (with or without hyphens)

    Returns:
        JSON object with page properties and metadata
    """
    page_id = page_id.replace("-", "")
    result = _make_request("GET", f"/pages/{page_id}")
    if not result["success"]:
        return json.dumps({"error": result["error"]})

    page = result["data"]
    return json.dumps({
        "id": page["id"],
        "created_time": page["created_time"],
        "last_edited_time": page["last_edited_time"],
        "archived": page.get("archived", False),
        "url": page.get("url", ""),
        "properties": _simplify_properties(page.get("properties", {})),
    }, indent=2)


@mcp.tool()
def notion_get_page_content(page_id: str) -> str:
    """Get page content blocks.

    Args:
        page_id: Page ID (with or without hyphens)

    Returns:
        JSON array of content blocks with text and type
    """
    page_id = page_id.replace("-", "")
    result = _make_request("GET", f"/blocks/{page_id}/children")
    if not result["success"]:
        return json.dumps({"error": result["error"]})

    blocks = [
        {
            "id": block["id"],
            "type": block["type"],
            "text": _extract_block_text(block),
        }
        for block in result["data"].get("results", [])
    ]
    return json.dumps(blocks, indent=2)


def _extract_block_text(block: dict[str, Any]) -> str:
    """Extract plain text from a block."""
    block_type = block.get("type")
    if not block_type:
        return ""

    block_content = block.get(block_type, {})
    rich_text = block_content.get("rich_text", [])

    if not rich_text:
        return ""

    return " ".join(item.get("plain_text", "") for item in rich_text)


def _simplify_properties(props: dict[str, Any]) -> dict[str, Any]:
    """Simplify property values for readability."""
    simplified = {}
    for key, prop in props.items():
        prop_type = prop.get("type")
        if prop_type == "title":
            title_arr = prop.get("title", [])
            simplified[key] = title_arr[0].get("plain_text", "") if title_arr else ""
        elif prop_type == "rich_text":
            text_arr = prop.get("rich_text", [])
            simplified[key] = text_arr[0].get("plain_text", "") if text_arr else ""
        elif prop_type == "number":
            simplified[key] = prop.get("number")
        elif prop_type == "select":
            select = prop.get("select")
            simplified[key] = select.get("name") if select else None
        elif prop_type == "multi_select":
            multi = prop.get("multi_select", [])
            simplified[key] = [item.get("name") for item in multi]
        elif prop_type == "date":
            date = prop.get("date")
            simplified[key] = date.get("start") if date else None
        elif prop_type == "checkbox":
            simplified[key] = prop.get("checkbox", False)
        elif prop_type == "url":
            simplified[key] = prop.get("url")
        elif prop_type == "email":
            simplified[key] = prop.get("email")
        elif prop_type == "phone_number":
            simplified[key] = prop.get("phone_number")
        else:
            simplified[key] = str(prop.get(prop_type, ""))

    return simplified


@mcp.tool()
def notion_create_page(
    parent_id: str,
    title: str,
    content: str = "",
) -> str:
    """Create a new page.

    Args:
        parent_id: Parent page or database ID
        title: Page title
        content: Page content (plain text, will be added as paragraph blocks)

    Returns:
        JSON object with created page details
    """
    parent_id = parent_id.replace("-", "")

    data: dict[str, Any] = {
        "parent": {"page_id": parent_id},
        "properties": {
            "title": {
                "title": [
                    {
                        "text": {"content": title}
                    }
                ]
            }
        },
    }

    if content:
        paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
        data["children"] = [
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [
                        {
                            "type": "text",
                            "text": {"content": para}
                        }
                    ]
                },
            }
            for para in paragraphs
        ]

    result = _make_request("POST", "/pages", json_data=data)
    if not result["success"]:
        return json.dumps({"error": result["error"]})

    page = result["data"]
    return json.dumps({
        "id": page["id"],
        "url": page.get("url", ""),
        "created_time": page["created_time"],
    }, indent=2)


@mcp.tool()
def notion_append_blocks(page_id: str, content: str) -> str:
    """Append content blocks to a page.

    Args:
        page_id: Page ID (with or without hyphens)
        content: Content to append (plain text, will be split into paragraphs)

    Returns:
        Success message or error
    """
    page_id = page_id.replace("-", "")

    paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
    children = [
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [
                    {
                        "type": "text",
                        "text": {"content": para}
                    }
                ]
            },
        }
        for para in paragraphs
    ]

    data = {"children": children}
    result = _make_request("PATCH", f"/blocks/{page_id}/children", json_data=data)
    if not result["success"]:
        return json.dumps({"error": result["error"]})

    return json.dumps({"success": True, "message": "Content appended"})


@mcp.tool()
def notion_update_page(
    page_id: str,
    title: str = "",
    archived: bool = False,
) -> str:
    """Update page properties.

    Args:
        page_id: Page ID (with or without hyphens)
        title: New title (optional)
        archived: Whether to archive the page

    Returns:
        Success message or error
    """
    page_id = page_id.replace("-", "")

    data: dict[str, Any] = {}

    if title:
        data["properties"] = {
            "title": {
                "title": [
                    {
                        "text": {"content": title}
                    }
                ]
            }
        }

    if archived:
        data["archived"] = True

    if not data:
        return json.dumps({"error": "No fields to update"})

    result = _make_request("PATCH", f"/pages/{page_id}", json_data=data)
    if not result["success"]:
        return json.dumps({"error": result["error"]})

    return json.dumps({"success": True, "message": "Page updated"})


@mcp.tool()
def notion_query_database(
    database_id: str,
    filter_property: str = "",
    filter_value: str = "",
    max_results: int = 100,
) -> str:
    """Query database for pages.

    Args:
        database_id: Database ID (with or without hyphens)
        filter_property: Property name to filter by (optional)
        filter_value: Value to filter for (optional)
        max_results: Maximum results (default 100)

    Returns:
        JSON array of database pages
    """
    database_id = database_id.replace("-", "")

    data: dict[str, Any] = {
        "page_size": min(max(1, max_results), 100),
    }

    if filter_property and filter_value:
        data["filter"] = {
            "property": filter_property,
            "rich_text": {
                "contains": filter_value
            }
        }

    result = _make_request("POST", f"/databases/{database_id}/query", json_data=data)
    if not result["success"]:
        return json.dumps({"error": result["error"]})

    pages = [
        {
            "id": page["id"],
            "url": page.get("url", ""),
            "properties": _simplify_properties(page.get("properties", {})),
            "created_time": page.get("created_time"),
            "last_edited_time": page.get("last_edited_time"),
        }
        for page in result["data"].get("results", [])
    ]
    return json.dumps(pages, indent=2)


if __name__ == "__main__":
    mcp.run()
