"""Foxwell MCP client.

Talks to the hosted Foxwell Founders MCP server over streamable HTTP with a
bearer token, calls `search_foxwell_knowledge`, and parses the formatted text
result into structured chunks.

The MCP tool returns one string:

    Found 12 results (confidence: high):

    [Source: #creative-strategy | Author: Jane | Date: 2026-05-02 | Age: 4mo ago | Relevance: 61% | Link: https://...]
    chunk text...

    ---

    [Source: SOP Database — Budget Scaling | Author: ... | Date: ... | FRESH | Relevance: 58% | Link: https://...]
    chunk text...

Blocks are separated by a blank line, three dashes, blank line. Each header is
one bracketed line of ` | ` separated fields. Fields with a colon are key:value,
fields without one are flags (FRESH, RECENT, AGING, STALE, FORMER MEMBER).
"""

from __future__ import annotations

import os
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, asdict

BLOCK_SEP = "\n\n---\n\n"
HEADER_RE = re.compile(r"^\[(?P<header>Source: .*?)\]\n(?P<body>.*)$", re.S)
FOUND_RE = re.compile(r"^Found (?P<n>\d+) results \(confidence: (?P<conf>\w+)\):\n\n", re.S)
FLAGS = {"FRESH", "RECENT", "AGING", "STALE", "FORMER MEMBER"}


@dataclass
class Chunk:
    source: str
    text: str
    author: str = ""
    date: str = ""
    age: str = ""
    freshness: str = ""
    relevance: float | None = None
    link: str = ""
    former_member: bool = False
    extra: dict = field(default_factory=dict)

    @property
    def source_type(self) -> str:
        s = self.source
        if s.startswith("#"):
            return "slack"
        for prefix, kind in (
            ("SOP Database", "gdrive"),
            ("Notion", "notion"),
            ("Blog", "blog"),
            ("Podcast Transcript", "podcast"),
            ("Call Transcript", "transcript"),
            ("X/Twitter", "social_x"),
            ("LinkedIn", "social_linkedin"),
            ("Shared Link", "shared_link"),
            ("Community discussion", "slack"),
        ):
            if s.startswith(prefix):
                return kind
        return "unknown"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["source_type"] = self.source_type
        return d


@dataclass
class SearchResult:
    query: str
    count: int
    confidence: str
    chunks: list[Chunk]
    raw: str
    error: str = ""  # e.g. "rate_limited"; chunks is empty when set


def parse_search_text(query: str, text: str) -> SearchResult:
    """Parse the MCP tool's formatted string into chunks."""
    original = text
    text = text.strip()
    count, confidence = 0, "none"
    m = FOUND_RE.match(text)
    if m:
        count = int(m.group("n"))
        confidence = m.group("conf")
        text = text[m.end():]
    elif text.startswith("No relevant community discussions"):
        return SearchResult(query, 0, "none", [], original)
    elif text.startswith("Rate limit exceeded"):
        return SearchResult(query, 0, "none", [], original, error="rate_limited")

    chunks: list[Chunk] = []
    for block in text.split(BLOCK_SEP):
        block = block.strip()
        hm = HEADER_RE.match(block)
        if not hm:
            continue
        chunks.append(_parse_header(hm.group("header"), hm.group("body").strip()))
    return SearchResult(query, count or len(chunks), confidence, chunks, original)


def _parse_header(header: str, body: str) -> Chunk:
    parts = [p.strip() for p in header.split(" | ")]
    c = Chunk(source="", text=body)
    for p in parts:
        if p in FLAGS:
            if p == "FORMER MEMBER":
                c.former_member = True
            else:
                c.freshness = p
            continue
        if ":" not in p:
            c.extra.setdefault("flags", []).append(p)
            continue
        key, _, val = p.partition(":")
        key, val = key.strip(), val.strip()
        if key == "Source":
            c.source = val
        elif key == "Author":
            c.author = val
        elif key == "Date":
            c.date = val
        elif key == "Age":
            c.age = val
        elif key == "Relevance":
            try:
                c.relevance = float(val.rstrip("%")) / 100
            except ValueError:
                pass
        elif key == "Link":
            c.link = val
        else:
            c.extra[key] = val
    return c


class FoxwellClient:
    """Thin async wrapper over the remote Foxwell MCP server."""

    def __init__(self, url: str | None = None, token: str | None = None):
        self.url = url or os.environ.get("FOXWELL_MCP_URL", "https://foxwell-bot.onrender.com/mcp")
        self.token = token or os.environ.get("FOXWELL_MCP_TOKEN", "")
        if not self.token:
            raise RuntimeError("FOXWELL_MCP_TOKEN is not set")

    @asynccontextmanager
    async def _session(self):
        """Open an MCP session. Supports mcp SDK 1.x and 2.x client APIs."""
        from mcp import ClientSession

        headers = {"Authorization": f"Bearer {self.token}"}
        try:  # mcp >= 2.0
            from mcp.client.streamable_http import streamable_http_client
            from mcp.shared._httpx_utils import create_mcp_http_client

            async with create_mcp_http_client(headers=headers) as http:
                async with streamable_http_client(self.url, http_client=http) as streams:
                    read, write = streams[0], streams[1]
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        yield session
        except ImportError:  # mcp 1.x
            from mcp.client.streamable_http import streamablehttp_client

            async with streamablehttp_client(self.url, headers=headers) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    yield session

    async def _call(self, tool: str, args: dict) -> str:
        async with self._session() as session:
            result = await session.call_tool(tool, args)
        texts = [c.text for c in result.content if getattr(c, "type", "") == "text"]
        return "\n".join(texts)

    async def search(self, query: str, source_filter: str | None = None) -> SearchResult:
        args: dict = {"query": query}
        if source_filter:
            args["source_filter"] = source_filter
        text = await self._call("search_foxwell_knowledge", args)
        return parse_search_text(query, text)

    async def list_tools(self) -> list[str]:
        async with self._session() as session:
            tools = await session.list_tools()
        return [t.name for t in tools.tools]
