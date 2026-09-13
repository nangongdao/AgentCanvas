"""Slack MCP server: messages, channels, and workspace operations."""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
from mcp.server.mcpserver import MCPServer

mcp = MCPServer("slack")


def _get_token() -> str:
    """Get Slack token from environment."""
    token = os.environ.get("SLACK_BOT_TOKEN", "")
    if not token:
        raise ValueError("SLACK_BOT_TOKEN environment variable not set")
    return token


def _make_request(
    method: str,
    params: dict[str, Any] | None = None,
    json_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Make Slack API request."""
    token = _get_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }
    url = f"https://slack.com/api/{method}"

    try:
        with httpx.Client(timeout=30.0) as client:
            if json_data:
                response = client.post(url, headers=headers, json=json_data)
            else:
                response = client.get(url, headers=headers, params=params or {})

            response.raise_for_status()
            data = response.json()

            if not data.get("ok"):
                return {
                    "success": False,
                    "error": data.get("error", "Unknown error"),
                }
            return {"success": True, "data": data}
    except httpx.HTTPStatusError as e:
        return {
            "success": False,
            "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}",
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
def slack_list_channels(types: str = "public_channel,private_channel") -> str:
    """List workspace channels.

    Args:
        types: Channel types to include (comma-separated):
               public_channel, private_channel, mpim, im

    Returns:
        JSON array of channels with id, name, and member count
    """
    result = _make_request(
        "conversations.list",
        params={"types": types, "limit": 1000},
    )
    if not result["success"]:
        return json.dumps({"error": result["error"]})

    channels = [
        {
            "id": ch["id"],
            "name": ch.get("name", "Direct Message"),
            "is_private": ch.get("is_private", False),
            "is_archived": ch.get("is_archived", False),
            "num_members": ch.get("num_members", 0),
            "topic": ch.get("topic", {}).get("value", ""),
        }
        for ch in result["data"].get("channels", [])
    ]
    return json.dumps(channels, indent=2)


@mcp.tool()
def slack_get_channel_info(channel_id: str) -> str:
    """Get detailed channel information.

    Args:
        channel_id: Channel ID (starts with C, G, or D)

    Returns:
        JSON object with channel details
    """
    result = _make_request("conversations.info", params={"channel": channel_id})
    if not result["success"]:
        return json.dumps({"error": result["error"]})

    ch = result["data"]["channel"]
    return json.dumps({
        "id": ch["id"],
        "name": ch.get("name", "Direct Message"),
        "is_private": ch.get("is_private", False),
        "is_archived": ch.get("is_archived", False),
        "topic": ch.get("topic", {}).get("value", ""),
        "purpose": ch.get("purpose", {}).get("value", ""),
        "num_members": ch.get("num_members", 0),
        "created": ch.get("created"),
    }, indent=2)


@mcp.tool()
def slack_send_message(channel_id: str, text: str, thread_ts: str = "") -> str:
    """Send a message to a channel or thread.

    Args:
        channel_id: Channel ID (starts with C, G, or D)
        text: Message text (markdown supported)
        thread_ts: Thread timestamp to reply in (optional)

    Returns:
        JSON object with sent message details
    """
    data: dict[str, Any] = {
        "channel": channel_id,
        "text": text,
    }
    if thread_ts:
        data["thread_ts"] = thread_ts

    result = _make_request("chat.postMessage", json_data=data)
    if not result["success"]:
        return json.dumps({"error": result["error"]})

    return json.dumps({
        "success": True,
        "channel": result["data"]["channel"],
        "ts": result["data"]["ts"],
        "message": result["data"]["message"],
    }, indent=2)


@mcp.tool()
def slack_get_messages(
    channel_id: str,
    limit: int = 100,
    oldest: str = "",
) -> str:
    """Get messages from a channel.

    Args:
        channel_id: Channel ID (starts with C, G, or D)
        limit: Number of messages to retrieve (max 1000)
        oldest: Timestamp to start from (optional, gets newer messages)

    Returns:
        JSON array of messages with text, user, and timestamp
    """
    params: dict[str, Any] = {
        "channel": channel_id,
        "limit": min(max(1, limit), 1000),
    }
    if oldest:
        params["oldest"] = oldest

    result = _make_request("conversations.history", params=params)
    if not result["success"]:
        return json.dumps({"error": result["error"]})

    messages = [
        {
            "ts": msg["ts"],
            "user": msg.get("user", "bot"),
            "text": msg.get("text", ""),
            "thread_ts": msg.get("thread_ts"),
            "reply_count": msg.get("reply_count", 0),
        }
        for msg in result["data"].get("messages", [])
    ]
    return json.dumps(messages, indent=2)


@mcp.tool()
def slack_get_thread_replies(channel_id: str, thread_ts: str, limit: int = 100) -> str:
    """Get replies in a thread.

    Args:
        channel_id: Channel ID (starts with C, G, or D)
        thread_ts: Thread parent message timestamp
        limit: Number of replies to retrieve (max 1000)

    Returns:
        JSON array of thread replies
    """
    params: dict[str, Any] = {
        "channel": channel_id,
        "ts": thread_ts,
        "limit": min(max(1, limit), 1000),
    }

    result = _make_request("conversations.replies", params=params)
    if not result["success"]:
        return json.dumps({"error": result["error"]})

    replies = [
        {
            "ts": msg["ts"],
            "user": msg.get("user", "bot"),
            "text": msg.get("text", ""),
        }
        for msg in result["data"].get("messages", [])
    ]
    return json.dumps(replies, indent=2)


@mcp.tool()
def slack_add_reaction(channel_id: str, timestamp: str, emoji: str) -> str:
    """Add an emoji reaction to a message.

    Args:
        channel_id: Channel ID (starts with C, G, or D)
        timestamp: Message timestamp
        emoji: Emoji name without colons (e.g., 'thumbsup', 'eyes')

    Returns:
        Success message or error
    """
    data = {
        "channel": channel_id,
        "timestamp": timestamp,
        "name": emoji,
    }

    result = _make_request("reactions.add", json_data=data)
    if not result["success"]:
        return json.dumps({"error": result["error"]})

    return json.dumps({"success": True, "message": "Reaction added"})


@mcp.tool()
def slack_search_messages(query: str, count: int = 20) -> str:
    """Search for messages across workspace.

    Args:
        query: Search query (supports Slack search syntax)
        count: Number of results (max 100)

    Returns:
        JSON array of matching messages
    """
    params = {
        "query": query,
        "count": min(max(1, count), 100),
    }

    result = _make_request("search.messages", params=params)
    if not result["success"]:
        return json.dumps({"error": result["error"]})

    messages = [
        {
            "text": msg["text"],
            "user": msg.get("username", "unknown"),
            "channel": msg["channel"]["name"],
            "ts": msg["ts"],
            "permalink": msg["permalink"],
        }
        for msg in result["data"].get("messages", {}).get("matches", [])
    ]
    return json.dumps(messages, indent=2)


@mcp.tool()
def slack_get_user_info(user_id: str) -> str:
    """Get user information.

    Args:
        user_id: User ID (starts with U)

    Returns:
        JSON object with user profile
    """
    result = _make_request("users.info", params={"user": user_id})
    if not result["success"]:
        return json.dumps({"error": result["error"]})

    user = result["data"]["user"]
    profile = user.get("profile", {})
    return json.dumps({
        "id": user["id"],
        "name": user.get("name"),
        "real_name": user.get("real_name"),
        "display_name": profile.get("display_name"),
        "email": profile.get("email"),
        "title": profile.get("title"),
        "is_bot": user.get("is_bot", False),
        "is_admin": user.get("is_admin", False),
        "timezone": user.get("tz"),
    }, indent=2)


@mcp.tool()
def slack_create_channel(name: str, is_private: bool = False) -> str:
    """Create a new channel.

    Args:
        name: Channel name (lowercase, no spaces, max 80 chars)
        is_private: Whether to create a private channel

    Returns:
        JSON object with created channel details
    """
    data = {
        "name": name,
        "is_private": is_private,
    }

    result = _make_request("conversations.create", json_data=data)
    if not result["success"]:
        return json.dumps({"error": result["error"]})

    ch = result["data"]["channel"]
    return json.dumps({
        "id": ch["id"],
        "name": ch["name"],
        "is_private": ch.get("is_private", False),
    }, indent=2)


@mcp.tool()
def slack_invite_to_channel(channel_id: str, user_ids: str) -> str:
    """Invite users to a channel.

    Args:
        channel_id: Channel ID (starts with C or G)
        user_ids: Comma-separated user IDs

    Returns:
        Success message or error
    """
    data = {
        "channel": channel_id,
        "users": user_ids,
    }

    result = _make_request("conversations.invite", json_data=data)
    if not result["success"]:
        return json.dumps({"error": result["error"]})

    return json.dumps({"success": True, "message": "Users invited"})


@mcp.tool()
def slack_set_channel_topic(channel_id: str, topic: str) -> str:
    """Set channel topic.

    Args:
        channel_id: Channel ID (starts with C or G)
        topic: New topic text (max 250 chars)

    Returns:
        Success message or error
    """
    data = {
        "channel": channel_id,
        "topic": topic[:250],
    }

    result = _make_request("conversations.setTopic", json_data=data)
    if not result["success"]:
        return json.dumps({"error": result["error"]})

    return json.dumps({"success": True, "message": "Topic updated"})


if __name__ == "__main__":
    mcp.run()
