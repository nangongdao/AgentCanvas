"""Jira MCP server — task and issue management via the Jira REST API.

Uses the MCP SDK 2.x ``MCPServer`` API (``@mcp.tool()``) so the file works
with the same SDK version as the other built-in servers.
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx

from mcp.server.mcpserver import MCPServer

mcp = MCPServer("jira")

_JIRA_URL = os.getenv("JIRA_URL", "")
_JIRA_EMAIL = os.getenv("JIRA_EMAIL", "")
_JIRA_API_TOKEN = os.getenv("JIRA_API_TOKEN", "")


def _configured() -> bool:
    return bool(_JIRA_URL and _JIRA_EMAIL and _JIRA_API_TOKEN)


async def _client() -> httpx.AsyncClient | None:
    if not _configured():
        return None
    return httpx.AsyncClient(
        auth=(_JIRA_EMAIL, _JIRA_API_TOKEN),
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )


def _jira_doc(text: str) -> dict[str, Any]:
    """Build an ADF (Atlassian Document Format) paragraph for plain text."""
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": text}],
            }
        ],
    }


@mcp.tool()
async def jira_create_issue(
    project_key: str,
    summary: str,
    description: str = "",
    issue_type: str = "Task",
    priority: str = "Medium",
) -> str:
    """Create a new Jira issue in a project."""
    client = await _client()
    if client is None:
        return "Error: Jira credentials not configured. Set JIRA_URL, JIRA_EMAIL, and JIRA_API_TOKEN."
    try:
        issue_data: dict[str, Any] = {
            "fields": {
                "project": {"key": project_key},
                "summary": summary,
                "description": _jira_doc(description),
                "issuetype": {"name": issue_type},
            }
        }
        issue_data["fields"]["priority"] = {"name": priority}
        response = await client.post(f"{_JIRA_URL}/rest/api/3/issue", json=issue_data)
        response.raise_for_status()
        result = response.json()
        return f"Created issue {result['key']}: {_JIRA_URL}/browse/{result['key']}"
    except Exception as e:
        return f"Jira API error: {str(e)}"
    finally:
        await client.aclose()


@mcp.tool()
async def jira_get_issue(issue_key: str) -> str:
    """Get details of a Jira issue by key (e.g. PROJ-123)."""
    client = await _client()
    if client is None:
        return "Error: Jira credentials not configured. Set JIRA_URL, JIRA_EMAIL, and JIRA_API_TOKEN."
    try:
        response = await client.get(f"{_JIRA_URL}/rest/api/3/issue/{issue_key}")
        response.raise_for_status()
        fields = response.json()["fields"]
        return json.dumps(
            {
                "key": issue_key,
                "summary": fields["summary"],
                "status": fields["status"]["name"],
                "assignee": fields.get("assignee", {}).get("displayName"),
                "priority": fields.get("priority", {}).get("name"),
                "created": fields["created"],
                "updated": fields["updated"],
            },
            indent=2,
        )
    except Exception as e:
        return f"Jira API error: {str(e)}"
    finally:
        await client.aclose()


@mcp.tool()
async def jira_update_issue(
    issue_key: str,
    summary: str | None = None,
    description: str | None = None,
    status: str | None = None,
    assignee: str | None = None,
) -> str:
    """Update an existing Jira issue's fields."""
    client = await _client()
    if client is None:
        return "Error: Jira credentials not configured. Set JIRA_URL, JIRA_EMAIL, and JIRA_API_TOKEN."
    try:
        update_data: dict[str, Any] = {"fields": {}}
        if summary is not None:
            update_data["fields"]["summary"] = summary
        if description is not None:
            update_data["fields"]["description"] = _jira_doc(description)
        if assignee is not None:
            update_data["fields"]["assignee"] = {"accountId": assignee}
        response = await client.put(f"{_JIRA_URL}/rest/api/3/issue/{issue_key}", json=update_data)
        response.raise_for_status()
        return f"Updated issue {issue_key}"
    except Exception as e:
        return f"Jira API error: {str(e)}"
    finally:
        await client.aclose()


@mcp.tool()
async def jira_search_issues(jql: str, max_results: int = 50) -> str:
    """Search Jira issues using JQL."""
    client = await _client()
    if client is None:
        return "Error: Jira credentials not configured. Set JIRA_URL, JIRA_EMAIL, and JIRA_API_TOKEN."
    try:
        response = await client.get(
            f"{_JIRA_URL}/rest/api/3/search",
            params={"jql": jql, "maxResults": max_results},
        )
        response.raise_for_status()
        result = response.json()
        issues = [
            {
                "key": issue["key"],
                "summary": issue["fields"]["summary"],
                "status": issue["fields"]["status"]["name"],
            }
            for issue in result["issues"]
        ]
        return f"Found {result['total']} issues:\n{json.dumps(issues, indent=2, ensure_ascii=False)}"
    except Exception as e:
        return f"Jira API error: {str(e)}"
    finally:
        await client.aclose()


@mcp.tool()
async def jira_add_comment(issue_key: str, comment: str) -> str:
    """Add a comment to a Jira issue."""
    client = await _client()
    if client is None:
        return "Error: Jira credentials not configured. Set JIRA_URL, JIRA_EMAIL, and JIRA_API_TOKEN."
    try:
        comment_data = {"body": _jira_doc(comment)}
        response = await client.post(
            f"{_JIRA_URL}/rest/api/3/issue/{issue_key}/comment", json=comment_data
        )
        response.raise_for_status()
        return f"Added comment to {issue_key}"
    except Exception as e:
        return f"Jira API error: {str(e)}"
    finally:
        await client.aclose()


@mcp.tool()
async def jira_transition_issue(issue_key: str, transition_name: str) -> str:
    """Transition an issue to a new status (e.g. 'To Do', 'In Progress', 'Done')."""
    client = await _client()
    if client is None:
        return "Error: Jira credentials not configured. Set JIRA_URL, JIRA_EMAIL, and JIRA_API_TOKEN."
    try:
        transitions_response = await client.get(
            f"{_JIRA_URL}/rest/api/3/issue/{issue_key}/transitions"
        )
        transitions_response.raise_for_status()
        transitions = transitions_response.json()["transitions"]

        transition_id = next(
            (
                t["id"]
                for t in transitions
                if t["name"].lower() == transition_name.lower()
            ),
            None,
        )
        if transition_id is None:
            available = [t["name"] for t in transitions]
            return f"Transition '{transition_name}' not available. Available: {', '.join(available)}"

        response = await client.post(
            f"{_JIRA_URL}/rest/api/3/issue/{issue_key}/transitions",
            json={"transition": {"id": transition_id}},
        )
        response.raise_for_status()
        return f"Transitioned {issue_key} to '{transition_name}'"
    except Exception as e:
        return f"Jira API error: {str(e)}"
    finally:
        await client.aclose()


if __name__ == "__main__":
    mcp.run()