# Hermes Agent bridge for Restaurant-claw

This exposes Restaurant-claw's existing tool layer to **Hermes Agent** (Nous
Research) over **MCP (stdio)**, so Hermes's autonomous agent loop can act as a
fourth "planner" that drives your real Cal.com / Twilio / Google / OSM /
EazyDiner integrations — without changing any of `agent_claw/`.

## What it does (and does not do)

- `mcp_server.py` is a thin stdio MCP server. It imports `TOOL_SCHEMAS` and
  `dispatch()` from `agent_claw.agent.tools` and forwards each tool call.
- It exposes **7 tools**: `get_user_location`, `search_restaurants`,
  `check_availability`, `book_reservation`, `send_sms`, `save_booking`,
  `create_calendar_event`.
- It does **not** expose `ask_human`. That tool is human-in-the-loop; Hermes
  handles human clarification with its own callbacks. See the docstring in
  `mcp_server.py` for the full rationale.
- Nothing in `providers/`, `storage/`, `config.py`, or `orchestrator.py`
  changes. Your three existing planners (rule / ollama / claude) keep working.

## Install

Run in the interpreter Hermes will use to launch the bridge (WSL2 — see
caveat below). Both the bridge dep and the project deps are required:

```bash
pip install -r hermes_bridge/requirements.txt
pip install -r requirements.txt          # the project's own providers
```

## Wire it into Hermes

Hermes reads MCP config from `~/.hermes/config.yaml`. Add:

```yaml
mcp_servers:
  restaurant:
    command: "python"
    args:
      - "/mnt/c/SATHYA/CHAINAIM3003/mcp-servers/Agent-claw/Restaurant-claw/hermes_bridge/mcp_server.py"
    tools:
      include:
        - get_user_location
        - search_restaurants
        - check_availability
        - book_reservation
        - send_sms
        - save_booking
        - create_calendar_event
```

Then in Hermes: `/reload-mcp` (or restart). The tools appear to the agent as
`mcp_restaurant_<tool_name>` in the toolset `mcp-restaurant`.

## Open runtime caveats (NOT yet resolved)

1. **WSL2 vs Windows.** Hermes's own site says native Windows support is
   experimental and recommends WSL2. The `args` path above uses `/mnt/c/...`
   for that reason. If you run Hermes in WSL2, the bridge subprocess runs in
   WSL2 too — which means the EazyDiner **Playwright** scraper needs browser
   binaries installed *in WSL2* (`playwright install`), and any Windows-style
   paths in `.env` (e.g. `GOOGLE_CLIENT_SECRETS_PATH`) must be valid from WSL2.
2. **Credentials.** `.env` at the repo root is auto-loaded by `config.py`. The
   WSL2 interpreter must be able to read it and reach the same services.

These are deployment questions, not code bugs. Decide the WSL2 story before
the first live booking test.
