"""Jira MCP Server - Task and issue management."""

import json
import os
from typing import Any

from mcp.server import Server
from mcp.types import Resource, Tool


def create_jira_server() -> Server:
    """Create Jira MCP server instance."""
    server = Server("jira")

    # Configuration from environment
    jira_url = os.getenv("JIRA_URL", "")
    jira_email = os.getenv("JIRA_EMAIL", "")
    jira_api_token = os.getenv("JIRA_API_TOKEN", "")

    @server.list_resources()
    async def list_resources() -> list[Resource]:
        """List available Jira resources."""
        return [
            Resource(
                uri="jira://projects",
                name="Jira Projects",
                mimeType="application/json",
                description="List all accessible Jira projects",
            ),
            Resource(
                uri="jira://issues/search",
                name="Issue Search",
                mimeType="application/json",
                description="Search Jira issues with JQL",
            ),
        ]

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        """List available Jira tools."""
        return [
            Tool(
                name="jira_create_issue",
                description="Create a new Jira issue",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "project_key": {
                            "type": "string",
                            "description": "Project key (e.g., 'PROJ')",
                        },
                        "summary": {
                            "type": "string",
                            "description": "Issue summary/title",
                        },
                        "description": {
                            "type": "string",
                            "description": "Issue description",
                        },
                        "issue_type": {
                            "type": "string",
                            "description": "Issue type (Task, Bug, Story, etc.)",
                            "default": "Task",
                        },
                        "priority": {
                            "type": "string",
                            "description": "Priority (Highest, High, Medium, Low, Lowest)",
                            "default": "Medium",
                        },
                    },
                    "required": ["project_key", "summary"],
                },
            ),
            Tool(
                name="jira_get_issue",
                description="Get details of a Jira issue by key",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "issue_key": {
                            "type": "string",
                            "description": "Issue key (e.g., 'PROJ-123')",
                        },
                    },
                    "required": ["issue_key"],
                },
            ),
            Tool(
                name="jira_update_issue",
                description="Update an existing Jira issue",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "issue_key": {
                            "type": "string",
                            "description": "Issue key to update",
                        },
                        "summary": {"type": "string"},
                        "description": {"type": "string"},
                        "status": {
                            "type": "string",
                            "description": "Status transition (To Do, In Progress, Done)",
                        },
                        "assignee": {
                            "type": "string",
                            "description": "Assignee account ID or email",
                        },
                    },
                    "required": ["issue_key"],
                },
            ),
            Tool(
                name="jira_search_issues",
                description="Search Jira issues using JQL",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "jql": {
                            "type": "string",
                            "description": "JQL query string",
                        },
                        "max_results": {
                            "type": "integer",
                            "description": "Maximum results to return",
                            "default": 50,
                        },
                    },
                    "required": ["jql"],
                },
            ),
            Tool(
                name="jira_add_comment",
                description="Add a comment to a Jira issue",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "issue_key": {
                            "type": "string",
                            "description": "Issue key",
                        },
                        "comment": {
                            "type": "string",
                            "description": "Comment text",
                        },
                    },
                    "required": ["issue_key", "comment"],
                },
            ),
            Tool(
                name="jira_transition_issue",
                description="Transition issue to a new status",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "issue_key": {
                            "type": "string",
                            "description": "Issue key",
                        },
                        "transition_name": {
                            "type": "string",
                            "description": "Transition name (e.g., 'To Do', 'In Progress', 'Done')",
                        },
                    },
                    "required": ["issue_key", "transition_name"],
                },
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: Any) -> list[Any]:
        """Handle tool execution."""
        if not all([jira_url, jira_email, jira_api_token]):
            return [
                {
                    "type": "text",
                    "text": "Error: Jira credentials not configured. Set JIRA_URL, JIRA_EMAIL, and JIRA_API_TOKEN.",
                }
            ]

        try:
            # Import httpx only when needed
            import httpx

            auth = (jira_email, jira_api_token)
            headers = {"Accept": "application/json", "Content-Type": "application/json"}

            async with httpx.AsyncClient() as client:
                if name == "jira_create_issue":
                    issue_data = {
                        "fields": {
                            "project": {"key": arguments["project_key"]},
                            "summary": arguments["summary"],
                            "description": {
                                "type": "doc",
                                "version": 1,
                                "content": [
                                    {
                                        "type": "paragraph",
                                        "content": [
                                            {
                                                "type": "text",
                                                "text": arguments.get("description", ""),
                                            }
                                        ],
                                    }
                                ],
                            },
                            "issuetype": {"name": arguments.get("issue_type", "Task")},
                        }
                    }

                    if "priority" in arguments:
                        issue_data["fields"]["priority"] = {"name": arguments["priority"]}

                    response = await client.post(
                        f"{jira_url}/rest/api/3/issue",
                        auth=auth,
                        headers=headers,
                        json=issue_data,
                    )
                    response.raise_for_status()
                    result = response.json()
                    return [
                        {
                            "type": "text",
                            "text": f"Created issue {result['key']}: {jira_url}/browse/{result['key']}",
                        }
                    ]

                elif name == "jira_get_issue":
                    response = await client.get(
                        f"{jira_url}/rest/api/3/issue/{arguments['issue_key']}",
                        auth=auth,
                        headers=headers,
                    )
                    response.raise_for_status()
                    issue = response.json()
                    fields = issue["fields"]
                    return [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {
                                    "key": issue["key"],
                                    "summary": fields["summary"],
                                    "status": fields["status"]["name"],
                                    "assignee": fields.get("assignee", {}).get("displayName"),
                                    "priority": fields.get("priority", {}).get("name"),
                                    "created": fields["created"],
                                    "updated": fields["updated"],
                                },
                                indent=2,
                            ),
                        }
                    ]

                elif name == "jira_update_issue":
                    update_data: dict[str, Any] = {"fields": {}}
                    if "summary" in arguments:
                        update_data["fields"]["summary"] = arguments["summary"]
                    if "description" in arguments:
                        update_data["fields"]["description"] = {
                            "type": "doc",
                            "version": 1,
                            "content": [
                                {
                                    "type": "paragraph",
                                    "content": [
                                        {"type": "text", "text": arguments["description"]}
                                    ],
                                }
                            ],
                        }
                    if "assignee" in arguments:
                        update_data["fields"]["assignee"] = {"accountId": arguments["assignee"]}

                    response = await client.put(
                        f"{jira_url}/rest/api/3/issue/{arguments['issue_key']}",
                        auth=auth,
                        headers=headers,
                        json=update_data,
                    )
                    response.raise_for_status()
                    return [{"type": "text", "text": f"Updated issue {arguments['issue_key']}"}]

                elif name == "jira_search_issues":
                    params = {
                        "jql": arguments["jql"],
                        "maxResults": arguments.get("max_results", 50),
                    }
                    response = await client.get(
                        f"{jira_url}/rest/api/3/search",
                        auth=auth,
                        headers=headers,
                        params=params,
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
                    return [
                        {
                            "type": "text",
                            "text": f"Found {result['total']} issues:\n{json.dumps(issues, indent=2)}",
                        }
                    ]

                elif name == "jira_add_comment":
                    comment_data = {
                        "body": {
                            "type": "doc",
                            "version": 1,
                            "content": [
                                {
                                    "type": "paragraph",
                                    "content": [{"type": "text", "text": arguments["comment"]}],
                                }
                            ],
                        }
                    }
                    response = await client.post(
                        f"{jira_url}/rest/api/3/issue/{arguments['issue_key']}/comment",
                        auth=auth,
                        headers=headers,
                        json=comment_data,
                    )
                    response.raise_for_status()
                    return [{"type": "text", "text": f"Added comment to {arguments['issue_key']}"}]

                elif name == "jira_transition_issue":
                    # Get available transitions
                    transitions_response = await client.get(
                        f"{jira_url}/rest/api/3/issue/{arguments['issue_key']}/transitions",
                        auth=auth,
                        headers=headers,
                    )
                    transitions_response.raise_for_status()
                    transitions = transitions_response.json()["transitions"]

                    # Find matching transition
                    transition_id = None
                    for trans in transitions:
                        if trans["name"].lower() == arguments["transition_name"].lower():
                            transition_id = trans["id"]
                            break

                    if not transition_id:
                        available = [t["name"] for t in transitions]
                        return [
                            {
                                "type": "text",
                                "text": f"Transition '{arguments['transition_name']}' not available. Available: {', '.join(available)}",
                            }
                        ]

                    # Execute transition
                    response = await client.post(
                        f"{jira_url}/rest/api/3/issue/{arguments['issue_key']}/transitions",
                        auth=auth,
                        headers=headers,
                        json={"transition": {"id": transition_id}},
                    )
                    response.raise_for_status()
                    return [
                        {
                            "type": "text",
                            "text": f"Transitioned {arguments['issue_key']} to '{arguments['transition_name']}'",
                        }
                    ]

                else:
                    return [{"type": "text", "text": f"Unknown tool: {name}"}]

        except ImportError:
            return [{"type": "text", "text": "Error: httpx not installed. Run: pip install httpx"}]
        except Exception as e:
            return [{"type": "text", "text": f"Jira API error: {str(e)}"}]

    return server
