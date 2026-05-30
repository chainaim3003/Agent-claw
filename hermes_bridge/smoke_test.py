"""Standalone smoke test for the Hermes bridge foundation.

This deliberately does NOT involve Hermes or MCP. It exercises the exact import
chain and dispatch path the bridge uses, via the one tool that is read-only and
needs no credentials: get_user_location (hits public Nominatim only).

If this prints a lat/lng, then in THIS interpreter:
  - agent_claw imports correctly
  - config.py auto-loaded .env
  - the provider layer + dispatch() work
...which means hermes_bridge/mcp_server.py will work here too, and the only
remaining unknown is the MCP wiring itself (confirm later with /reload-mcp).

Run it in the SAME interpreter Hermes will use to launch the bridge (i.e. your
WSL2 python if you run Hermes in WSL2):

    python hermes_bridge/smoke_test.py
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _never(reason: str) -> dict:
    raise RuntimeError(f"ask_human should not fire in smoke test: {reason!r}")


def main() -> int:
    try:
        from agent_claw.agent.tools import dispatch
    except Exception as e:  # import chain broken
        print(f"FAIL: could not import agent_claw.agent.tools -> {type(e).__name__}: {e}")
        print("  Check: are project deps installed in THIS interpreter? "
              "Is the repo root on sys.path?")
        return 1

    address = "Bandra West, Mumbai"
    print(f"Calling get_user_location({address!r}) via dispatch()...")
    result = dispatch("get_user_location", {"address": address}, _never)

    if isinstance(result, dict) and result.get("is_error"):
        print(f"FAIL: provider returned an error -> {result.get('error')}")
        print("  (Network/Nominatim issue, or NOMINATIM_UA not set.)")
        return 1

    if isinstance(result, dict) and "lat" in result and "lng" in result:
        print(f"OK: {result.get('label')} -> ({result['lat']}, {result['lng']})")
        print("Foundation verified. The MCP bridge will run in this interpreter.")
        return 0

    print(f"UNEXPECTED result shape: {result!r}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
