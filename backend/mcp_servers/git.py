"""Git MCP server: repository operations, commits, branches, and status."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

mcp = MCPServer("git")


def _run_git(args: list[str], cwd: str | None = None) -> dict[str, Any]:
    """Run git command and return result."""
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        return {
            "success": result.returncode == 0,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
            "returncode": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "stdout": "",
            "stderr": "Command timed out after 30 seconds",
            "returncode": -1,
        }
    except Exception as e:
        return {
            "success": False,
            "stdout": "",
            "stderr": str(e),
            "returncode": -1,
        }


@mcp.tool()
def git_status(repo_path: str) -> str:
    """Get git repository status.

    Args:
        repo_path: Path to git repository

    Returns:
        Git status output including branch, changes, and untracked files
    """
    result = _run_git(["status", "--porcelain", "--branch"], cwd=repo_path)
    if not result["success"]:
        return f"Error: {result['stderr']}"
    return result["stdout"]


@mcp.tool()
def git_log(repo_path: str, max_count: int = 10) -> str:
    """Get git commit history.

    Args:
        repo_path: Path to git repository
        max_count: Maximum number of commits to show (default 10, max 100)

    Returns:
        Commit history with hash, author, date, and message
    """
    max_count = min(max(1, max_count), 100)
    result = _run_git(
        ["log", f"-{max_count}", "--pretty=format:%H|%an|%ad|%s", "--date=iso"],
        cwd=repo_path,
    )
    if not result["success"]:
        return f"Error: {result['stderr']}"
    return result["stdout"]


@mcp.tool()
def git_diff(repo_path: str, path: str = "") -> str:
    """Show changes in working directory.

    Args:
        repo_path: Path to git repository
        path: Optional file/directory path to limit diff

    Returns:
        Unified diff output
    """
    args = ["diff"]
    if path:
        args.append(path)
    result = _run_git(args, cwd=repo_path)
    if not result["success"]:
        return f"Error: {result['stderr']}"
    return result["stdout"] if result["stdout"] else "No changes"


@mcp.tool()
def git_branch_list(repo_path: str) -> str:
    """List all branches.

    Args:
        repo_path: Path to git repository

    Returns:
        List of branches with current branch marked by *
    """
    result = _run_git(["branch", "-a"], cwd=repo_path)
    if not result["success"]:
        return f"Error: {result['stderr']}"
    return result["stdout"]


@mcp.tool()
def git_branch_create(repo_path: str, branch_name: str, from_ref: str = "") -> str:
    """Create a new branch.

    Args:
        repo_path: Path to git repository
        branch_name: Name of new branch
        from_ref: Optional ref to branch from (commit hash, branch, or tag)

    Returns:
        Success message or error
    """
    args = ["branch", branch_name]
    if from_ref:
        args.append(from_ref)
    result = _run_git(args, cwd=repo_path)
    if not result["success"]:
        return f"Error: {result['stderr']}"
    return f"Branch '{branch_name}' created successfully"


@mcp.tool()
def git_checkout(repo_path: str, ref: str) -> str:
    """Checkout a branch, commit, or tag.

    Args:
        repo_path: Path to git repository
        ref: Branch name, commit hash, or tag to checkout

    Returns:
        Success message or error
    """
    result = _run_git(["checkout", ref], cwd=repo_path)
    if not result["success"]:
        return f"Error: {result['stderr']}"
    return f"Switched to '{ref}'"


@mcp.tool()
def git_add(repo_path: str, paths: str) -> str:
    """Stage files for commit.

    Args:
        repo_path: Path to git repository
        paths: Space-separated list of file paths to stage (use '.' for all)

    Returns:
        Success message or error
    """
    path_list = paths.split()
    result = _run_git(["add"] + path_list, cwd=repo_path)
    if not result["success"]:
        return f"Error: {result['stderr']}"
    return f"Staged {len(path_list)} path(s)"


@mcp.tool()
def git_commit(repo_path: str, message: str) -> str:
    """Create a commit with staged changes.

    Args:
        repo_path: Path to git repository
        message: Commit message

    Returns:
        Commit hash and summary or error
    """
    result = _run_git(["commit", "-m", message], cwd=repo_path)
    if not result["success"]:
        return f"Error: {result['stderr']}"
    return result["stdout"]


@mcp.tool()
def git_push(repo_path: str, remote: str = "origin", branch: str = "") -> str:
    """Push commits to remote repository.

    Args:
        repo_path: Path to git repository
        remote: Remote name (default: origin)
        branch: Branch to push (default: current branch)

    Returns:
        Push result or error
    """
    args = ["push", remote]
    if branch:
        args.append(branch)
    result = _run_git(args, cwd=repo_path)
    if not result["success"]:
        return f"Error: {result['stderr']}"
    return result["stdout"] if result["stdout"] else "Push successful"


@mcp.tool()
def git_pull(repo_path: str, remote: str = "origin", branch: str = "") -> str:
    """Pull changes from remote repository.

    Args:
        repo_path: Path to git repository
        remote: Remote name (default: origin)
        branch: Branch to pull (default: current branch)

    Returns:
        Pull result or error
    """
    args = ["pull", remote]
    if branch:
        args.append(branch)
    result = _run_git(args, cwd=repo_path)
    if not result["success"]:
        return f"Error: {result['stderr']}"
    return result["stdout"]


@mcp.tool()
def git_show(repo_path: str, ref: str) -> str:
    """Show commit details.

    Args:
        repo_path: Path to git repository
        ref: Commit hash, branch, or tag

    Returns:
        Commit details and diff
    """
    result = _run_git(["show", ref, "--stat"], cwd=repo_path)
    if not result["success"]:
        return f"Error: {result['stderr']}"
    return result["stdout"]


@mcp.tool()
def git_remote_list(repo_path: str) -> str:
    """List remote repositories.

    Args:
        repo_path: Path to git repository

    Returns:
        List of remotes with URLs
    """
    result = _run_git(["remote", "-v"], cwd=repo_path)
    if not result["success"]:
        return f"Error: {result['stderr']}"
    return result["stdout"] if result["stdout"] else "No remotes configured"


if __name__ == "__main__":
    mcp.run()
