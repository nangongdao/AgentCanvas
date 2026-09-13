"""GitHub MCP server: issues, pull requests, and repository operations."""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
from mcp.server.mcpserver import MCPServer

mcp = MCPServer("github")


def _get_token() -> str:
    """Get GitHub token from environment."""
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise ValueError("GITHUB_TOKEN environment variable not set")
    return token


def _make_request(
    method: str,
    endpoint: str,
    params: dict[str, Any] | None = None,
    json_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Make GitHub API request."""
    token = _get_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    url = f"https://api.github.com{endpoint}"

    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.request(
                method,
                url,
                headers=headers,
                params=params,
                json=json_data,
            )
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
def github_list_issues(
    repo: str,
    state: str = "open",
    labels: str = "",
    max_count: int = 30,
) -> str:
    """List repository issues.

    Args:
        repo: Repository in format 'owner/repo'
        state: Issue state (open, closed, all)
        labels: Comma-separated label names to filter by
        max_count: Maximum number of issues (default 30, max 100)

    Returns:
        JSON array of issues with number, title, state, and author
    """
    params: dict[str, Any] = {
        "state": state,
        "per_page": min(max(1, max_count), 100),
    }
    if labels:
        params["labels"] = labels

    result = _make_request("GET", f"/repos/{repo}/issues", params=params)
    if not result["success"]:
        return json.dumps({"error": result["error"]})

    issues = [
        {
            "number": issue["number"],
            "title": issue["title"],
            "state": issue["state"],
            "author": issue["user"]["login"],
            "labels": [label["name"] for label in issue.get("labels", [])],
            "created_at": issue["created_at"],
            "url": issue["html_url"],
        }
        for issue in result["data"]
        if "pull_request" not in issue  # Filter out PRs
    ]
    return json.dumps(issues, indent=2)


@mcp.tool()
def github_get_issue(repo: str, issue_number: int) -> str:
    """Get detailed information about an issue.

    Args:
        repo: Repository in format 'owner/repo'
        issue_number: Issue number

    Returns:
        JSON object with issue details including body text
    """
    result = _make_request("GET", f"/repos/{repo}/issues/{issue_number}")
    if not result["success"]:
        return json.dumps({"error": result["error"]})

    issue = result["data"]
    return json.dumps({
        "number": issue["number"],
        "title": issue["title"],
        "body": issue.get("body", ""),
        "state": issue["state"],
        "author": issue["user"]["login"],
        "labels": [label["name"] for label in issue.get("labels", [])],
        "comments": issue["comments"],
        "created_at": issue["created_at"],
        "updated_at": issue["updated_at"],
        "url": issue["html_url"],
    }, indent=2)


@mcp.tool()
def github_create_issue(
    repo: str,
    title: str,
    body: str = "",
    labels: str = "",
) -> str:
    """Create a new issue.

    Args:
        repo: Repository in format 'owner/repo'
        title: Issue title
        body: Issue description (markdown supported)
        labels: Comma-separated label names

    Returns:
        JSON object with created issue details
    """
    data: dict[str, Any] = {"title": title}
    if body:
        data["body"] = body
    if labels:
        data["labels"] = [label.strip() for label in labels.split(",")]

    result = _make_request("POST", f"/repos/{repo}/issues", json_data=data)
    if not result["success"]:
        return json.dumps({"error": result["error"]})

    issue = result["data"]
    return json.dumps({
        "number": issue["number"],
        "title": issue["title"],
        "url": issue["html_url"],
        "state": issue["state"],
    }, indent=2)


@mcp.tool()
def github_update_issue(
    repo: str,
    issue_number: int,
    title: str = "",
    body: str = "",
    state: str = "",
) -> str:
    """Update an existing issue.

    Args:
        repo: Repository in format 'owner/repo'
        issue_number: Issue number
        title: New title (optional)
        body: New body (optional)
        state: New state: open or closed (optional)

    Returns:
        Success message or error
    """
    data: dict[str, Any] = {}
    if title:
        data["title"] = title
    if body:
        data["body"] = body
    if state:
        data["state"] = state

    if not data:
        return json.dumps({"error": "No fields to update"})

    result = _make_request("PATCH", f"/repos/{repo}/issues/{issue_number}", json_data=data)
    if not result["success"]:
        return json.dumps({"error": result["error"]})

    return json.dumps({"success": True, "message": "Issue updated"})


@mcp.tool()
def github_list_prs(
    repo: str,
    state: str = "open",
    max_count: int = 30,
) -> str:
    """List repository pull requests.

    Args:
        repo: Repository in format 'owner/repo'
        state: PR state (open, closed, all)
        max_count: Maximum number of PRs (default 30, max 100)

    Returns:
        JSON array of pull requests with number, title, and state
    """
    params: dict[str, Any] = {
        "state": state,
        "per_page": min(max(1, max_count), 100),
    }

    result = _make_request("GET", f"/repos/{repo}/pulls", params=params)
    if not result["success"]:
        return json.dumps({"error": result["error"]})

    prs = [
        {
            "number": pr["number"],
            "title": pr["title"],
            "state": pr["state"],
            "author": pr["user"]["login"],
            "base": pr["base"]["ref"],
            "head": pr["head"]["ref"],
            "created_at": pr["created_at"],
            "url": pr["html_url"],
        }
        for pr in result["data"]
    ]
    return json.dumps(prs, indent=2)


@mcp.tool()
def github_get_pr(repo: str, pr_number: int) -> str:
    """Get detailed information about a pull request.

    Args:
        repo: Repository in format 'owner/repo'
        pr_number: Pull request number

    Returns:
        JSON object with PR details including description and review status
    """
    result = _make_request("GET", f"/repos/{repo}/pulls/{pr_number}")
    if not result["success"]:
        return json.dumps({"error": result["error"]})

    pr = result["data"]
    return json.dumps({
        "number": pr["number"],
        "title": pr["title"],
        "body": pr.get("body", ""),
        "state": pr["state"],
        "author": pr["user"]["login"],
        "base": pr["base"]["ref"],
        "head": pr["head"]["ref"],
        "mergeable": pr.get("mergeable"),
        "merged": pr.get("merged", False),
        "comments": pr["comments"],
        "commits": pr["commits"],
        "additions": pr["additions"],
        "deletions": pr["deletions"],
        "changed_files": pr["changed_files"],
        "created_at": pr["created_at"],
        "updated_at": pr["updated_at"],
        "url": pr["html_url"],
    }, indent=2)


@mcp.tool()
def github_create_pr(
    repo: str,
    title: str,
    head: str,
    base: str,
    body: str = "",
) -> str:
    """Create a new pull request.

    Args:
        repo: Repository in format 'owner/repo'
        title: PR title
        head: Branch containing changes
        base: Base branch to merge into
        body: PR description (markdown supported)

    Returns:
        JSON object with created PR details
    """
    data: dict[str, Any] = {
        "title": title,
        "head": head,
        "base": base,
    }
    if body:
        data["body"] = body

    result = _make_request("POST", f"/repos/{repo}/pulls", json_data=data)
    if not result["success"]:
        return json.dumps({"error": result["error"]})

    pr = result["data"]
    return json.dumps({
        "number": pr["number"],
        "title": pr["title"],
        "url": pr["html_url"],
        "state": pr["state"],
    }, indent=2)


@mcp.tool()
def github_add_comment(
    repo: str,
    issue_number: int,
    body: str,
) -> str:
    """Add a comment to an issue or pull request.

    Args:
        repo: Repository in format 'owner/repo'
        issue_number: Issue or PR number
        body: Comment text (markdown supported)

    Returns:
        Success message or error
    """
    data = {"body": body}
    result = _make_request(
        "POST",
        f"/repos/{repo}/issues/{issue_number}/comments",
        json_data=data,
    )
    if not result["success"]:
        return json.dumps({"error": result["error"]})

    return json.dumps({"success": True, "message": "Comment added"})


@mcp.tool()
def github_get_repo(repo: str) -> str:
    """Get repository information.

    Args:
        repo: Repository in format 'owner/repo'

    Returns:
        JSON object with repository details
    """
    result = _make_request("GET", f"/repos/{repo}")
    if not result["success"]:
        return json.dumps({"error": result["error"]})

    repo_data = result["data"]
    return json.dumps({
        "name": repo_data["name"],
        "full_name": repo_data["full_name"],
        "description": repo_data.get("description", ""),
        "private": repo_data["private"],
        "language": repo_data.get("language"),
        "stars": repo_data["stargazers_count"],
        "forks": repo_data["forks_count"],
        "open_issues": repo_data["open_issues_count"],
        "default_branch": repo_data["default_branch"],
        "created_at": repo_data["created_at"],
        "updated_at": repo_data["updated_at"],
        "url": repo_data["html_url"],
    }, indent=2)


if __name__ == "__main__":
    mcp.run()
