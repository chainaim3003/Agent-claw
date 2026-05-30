"""MCP stdio server that exposes Restaurant-claw's existing tool layer to
Hermes Agent (Nous Research).

Design notes
------------
- This is a thin bridge. It does NOT reimplement any tool. It imports the
  canonical TOOL_SCHEMAS and dispatch() from agent_claw.agent.tools and
  forwards each Hermes tool call straight into dispatch(). Your providers/,
  storage/, and config.py are untouched and remain the single source of truth.

- 7 of your 8 tools are exposed. `ask_human` is intentionally NOT bridged:
  it exists only to pause and ask the end user how to proceed, via the
  `ask_human_fn` callback in dispatch(). Over an MCP subprocess there is no
  channel to your human. Hermes is the component that talks to the human and
  has its own clarify/approval callbacks, so it handles that natively. The
  stub below is passed to dispatch() to satisfy its signature but is never
  invoked for the 7 bridged tools.

- Credentials: agent_claw/config.py auto-loads Restaurant-claw/.env lazily on
  first get_settings() call (via python-dotenv). So this bridge does not load
  .env itself; the providers do it on demand. The interpreter that runs this
  file MUST have the project's dependencies installed (including python-dotenv
  and, for the EazyDiner scraper, Playwright + its browser binaries).

- Tool naming: Hermes registers these under the prefix derived from the
  config.yaml key, i.e. with key `restaurant` they appear to Hermes's loop as
  `mcp_restaurant_<tool_name>` and as a toolset `mcp-restaurant`.

Run (for a manual smoke test; normally Hermes launches this as a subprocess):
    python hermes_bridge/mcp_server.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Make the project importable when Hermes launches this file directly.
# Restaurant-claw/ is the repo root; agent_claw is the package under it.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent_claw.agent.tools import TOOL_SCHEMAS, dispatch, to_json  # noqa: E402

from mcp.server import Server  # noqa: E402
from mcp.server.stdio import stdio_server  # noqa: E402
import mcp.types as types  # noqa: E402

SERVER_NAME = "restaurant-claw"

# Tools we do NOT expose over MCP. ask_human is human-in-the-loop and is
# Hermes's responsibility, not the bridge's.
EXCLUDED_TOOLS = {"ask_human"}


def _stub_ask_human(reason: str) -> dict:
    """Should never run: ask_human is not exposed over MCP. If it ever fires,
    fail loudly rather than silently returning a wrong answer."""
    raise RuntimeError(
        "ask_human was invoked inside the MCP bridge, but it is intentionally "
        "not exposed. Hermes should handle human clarification natively. "
        f"reason={reason!r}"
    )


def _bridged_tools() -> list[types.Tool]:
    """Translate the canonical OpenAI-shape schemas into MCP Tool objects,
    skipping excluded tools. The MCP `inputSchema` is exactly your existing
    JSON Schema `parameters` block."""
    tools: list[types.Tool] = []
    for entry in TOOL_SCHEMAS:
        fn = entry["function"]
        if fn["name"] in EXCLUDED_TOOLS:
            continue
        tools.append(
            types.Tool(
                name=fn["name"],
                description=fn["description"],
                inputSchema=fn["parameters"],
            )
        )
    return tools


app = Server(SERVER_NAME)


@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return _bridged_tools()


@app.call_tool()
async def call_tool(name: str, arguments: dict | None) -> list[types.TextContent]:
    args = arguments or {}

    if name in EXCLUDED_TOOLS:
        return [types.TextContent(
            type="text",
            text=to_json({"error": f"tool {name!r} is not exposed via the bridge",
                          "is_error": True}),
        )]

    # dispatch() is synchronous and may block on network/Playwright. Run it in
    # a worker thread so the stdio event loop stays responsive.
    try:
        result = await asyncio.to_thread(dispatch, name, args, _stub_ask_human)
    except Exception as e:  # last-resort guard so the server process survives
        result = {"error": f"{type(e).__name__}: {e}", "is_error": True}

    return [types.TextContent(type="text", text=to_json(result))]


async def main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
