"""Demo MCP server: web search via DuckDuckGo Instant Answer API (stdio).

No API key required. Falls back to a helpful message when offline.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request

from mcp.server.mcpserver import MCPServer

mcp = MCPServer("websearch")

_TIMEOUT = 10.0


@mcp.tool()
def search(query: str, max_results: int = 5) -> str:
    """Search the web (DuckDuckGo Instant Answers) and return text snippets."""
    url = (
        "https://api.duckduckgo.com/?"
        + urllib.parse.urlencode(
            {"q": query, "format": "json", "no_redirect": "1", "no_html": "1"}
        )
    )
    try:
        with urllib.request.urlopen(url, timeout=_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except OSError as exc:
        return f"search unavailable (network error: {exc})"

    lines: list[str] = []
    if data.get("AbstractText"):
        lines.append(f"Summary: {data['AbstractText']}")
        if data.get("AbstractURL"):
            lines.append(f"Source: {data['AbstractURL']}")
    for topic in data.get("RelatedTopics", [])[:max_results]:
        text = topic.get("Text") if isinstance(topic, dict) else None
        if text:
            lines.append(f"- {text}")
    if data.get("Answer"):
        lines.insert(0, f"Answer: {data['Answer']}")
    return "\n".join(lines) or f"no instant results for: {query}"


if __name__ == "__main__":
    mcp.run()
