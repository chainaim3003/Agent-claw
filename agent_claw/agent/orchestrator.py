"""Three interchangeable orchestrators over the same real tools:

  run_rule    deterministic state machine that walks the diagram. No LLM.
              Fastest, most predictable. Useful for validation and as the
              fallback when no LLM is configured.
  run_ollama  OpenAI-compatible chat-completions call against a local Ollama
              server. Free, fully local. Default planner.
  run_claude  Anthropic Messages API manual tool-use loop. Requires
              ANTHROPIC_API_KEY.

The ask_human pause is shared. In CLI it reads stdin; in a Twilio-webhook
deployment the same seam would be replaced by 'wait for next inbound SMS'."""
from __future__ import annotations
from dataclasses import dataclass, field
import json
import os

import requests

from ..config import get_settings
from ..http_client import request as http_request
from ..logging_setup import get_logger
from ..providers import (get_user_location, search_restaurants,
                         check_availability, book_reservation, send_sms,
                         send_email,
                         create_calendar_event,
                         ProviderError)
from ..storage import save_booking
from .prompts import SYSTEM_PROMPT
from .tools import TOOL_SCHEMAS, schemas_anthropic, dispatch, to_json

log = get_logger("agent")
MAX_ITERS = 16


@dataclass
class Context:
    """Per-run request + mutable session state."""
    query: str = ""
    address: str = ""
    cuisine: str | None = None
    party: int = 2
    date: str = ""              # YYYY-MM-DD
    time_window: str = "19:30"  # HH:MM local
    contact: str = ""           # E.164
    nearest_n: int = 5
    radius_m: int = 3000
    attempt: int = 1
    log: list[str] = field(default_factory=list)

    def trace(self, node: str, detail: str = "") -> None:
        line = f"[{node}] {detail}".rstrip()
        self.log.append(line)
        print(line)


# --- shared HITL --------------------------------------------------------------
def ask_human(reason: str) -> dict:
    print(f"\n  [ask_human] {reason}")
    print("  options: widen | change_time | change_cuisine | cancel")
    choice = (input("  your choice> ").strip().lower() or "cancel")
    changed: dict = {}
    if choice == "widen":
        changed = {"radius_m_increment": 2000, "cuisine": None}
    elif choice == "change_time":
        nt = input("  new time (HH:MM)> ").strip() or "20:30"
        changed = {"time_window": nt}
    elif choice == "change_cuisine":
        nc = (input("  new cuisine> ").strip().lower() or None)
        changed = {"cuisine": nc}
    elif choice != "cancel":
        choice = "change_time"
        changed = {"time_window": "20:30"}
    return {"choice": choice, "params_changed": changed}


# --- rule planner -------------------------------------------------------------
def run_rule(ctx: Context) -> dict | None:
    ctx.trace("User intent",
              f"query={ctx.query!r} address={ctx.address!r} "
              f"cuisine={ctx.cuisine} party={ctx.party} "
              f"date={ctx.date} time={ctx.time_window}")
    try:
        loc = get_user_location(ctx.address)
    except ProviderError as e:
        ctx.trace("get_user_location", f"FAILED: {e}")
        return None
    ctx.trace("get_user_location", f"{loc['label']} ({loc['lat']},{loc['lng']})")

    chosen: tuple[dict, str] | None = None
    while chosen is None:
        try:
            results = search_restaurants(loc["lat"], loc["lng"],
                                         cuisine=ctx.cuisine,
                                         limit=ctx.nearest_n,
                                         radius_m=ctx.radius_m,
                                         address=ctx.address)
        except ProviderError as e:
            ctx.trace("search_restaurants", f"FAILED: {e}")
            return None
        ctx.trace("search_restaurants",
                  f"{len(results)} found -> " +
                  ", ".join(_brief(r) for r in results))
        if not results:
            ctx.trace("Slot found?", "no candidates")
            if not _hitl_retry(ctx):
                return None
            continue

        for r in results:
            try:
                slots = check_availability(r, ctx.date, ctx.time_window, ctx.party)
            except ProviderError as e:
                ctx.trace("check_availability", f"{r['name']}: FAILED: {e}")
                continue
            ctx.trace("check_availability",
                      f"{r['name']}: {len(slots)} slot(s)" +
                      (f" first={slots[0]}" if slots else ""))
            if slots:
                chosen = (r, slots[0])
                break

        if chosen:
            ctx.trace("Slot found?", "yes")
        else:
            ctx.trace("Slot found?", "no")
            if not _hitl_retry(ctx):
                return None

    rest, slot = chosen
    try:
        booking = book_reservation(rest, ctx.date, slot, ctx.party, ctx.contact)
    except ProviderError as e:
        ctx.trace("book_reservation", f"FAILED: {e}")
        return None
    ctx.trace("book_reservation",
              f"{booking['confirmation_id']} {rest['name']} @ {slot}")

    # Keep SMS body short to fit in 1 segment (160 GSM chars) AFTER Twilio's
    # trial prefix "Sent from your Twilio trial account - " (~38 chars). With
    # a 2-segment body containing a URL, Indian carriers heavily filter
    # US-origin trial SMS. The EazyDiner URL still lives in the Google
    # Calendar event description; the SMS just confirms the booking.
    body_lines = [
        f"Booked {rest['name'][:30]} {ctx.date} {slot[11:16]}.",
        f"Ref {booking['confirmation_id']}",
    ]
    body = " ".join(body_lines)
    # Calendar event still gets the full enrichment (rating, EazyDiner URL).
    if rest.get("eazydiner_url"):
        booking["eazydiner_url"] = rest["eazydiner_url"]
    if rest.get("rating"):
        booking["rating"] = rest["rating"]
    try:
        sms = send_sms(ctx.contact, body)
        ctx.trace("send_sms", f"status={sms['status']} sid={sms['sid']} body_len={len(body)}")
    except ProviderError as e:
        ctx.trace("send_sms", f"FAILED (non-fatal): {e}")

    # --- email BOTH sides: customer + restaurant (non-fatal, like SMS) -------
    # Recipients are configurable via .env; defaults are the addresses you gave.
    cust_email = (os.environ.get("CUSTOMER_EMAIL") or "nishanthini01ai@gmail.com").strip()
    rest_email = (os.environ.get("RESTAURANT_EMAIL") or "kalaivanimanickam865@gmail.com").strip()
    when = f"{ctx.date} {slot[11:16]}"
    cust_subject = f"Reservation confirmed - {rest['name']} on {when}"
    cust_body = (
        "Hi,\n\nYour table is confirmed.\n\n"
        f"  Restaurant  : {rest['name']}\n"
        f"  Date / time : {when}\n"
        f"  Party size  : {ctx.party}\n"
        f"  Confirmation: {booking['confirmation_id']}\n"
        + (f"  Details     : {rest['eazydiner_url']}\n" if rest.get("eazydiner_url") else "")
        + "\nThank you,\nAgent-Claw Reservations\n"
    )
    rest_subject = (
        f"New booking - {ctx.party} guest(s) on {when} "
        f"(ref {booking['confirmation_id']})"
    )
    rest_body = (
        "New reservation received via Agent-Claw.\n\n"
        f"  Restaurant  : {rest['name']}\n"
        f"  Date / time : {when}\n"
        f"  Party size  : {ctx.party}\n"
        f"  Customer    : {ctx.contact}\n"
        f"  Confirmation: {booking['confirmation_id']}\n"
        "\nPlease prepare the table.\n-- Agent-Claw\n"
    )
    for who, addr, subj, ebody in (
        ("customer", cust_email, cust_subject, cust_body),
        ("restaurant", rest_email, rest_subject, rest_body),
    ):
        try:
            er = send_email(addr, subj, ebody)
            ctx.trace("send_email", f"{who} -> {addr} status={er['status']}")
        except ProviderError as e:
            ctx.trace("send_email", f"{who} -> {addr} FAILED (non-fatal): {e}")

    saved = save_booking(booking)
    ctx.trace("save_booking", f"row={saved['row_id']} invoice={saved['invoice_path']}")

    # Google Calendar: non-fatal, like SMS. If unconfigured, returns skipped:true.
    try:
        cal = create_calendar_event(booking)
        if cal.get("skipped"):
            ctx.trace("create_calendar_event", f"skipped: {cal.get('reason')}")
        else:
            ctx.trace("create_calendar_event",
                      f"event={cal['event_id']} link={cal['html_link']}")
    except ProviderError as e:
        ctx.trace("create_calendar_event", f"FAILED (non-fatal): {e}")

    ctx.trace("Done", f"confirmed {booking['confirmation_id']}")
    return booking


def _hitl_retry(ctx: Context) -> bool:
    """Returns True if we should retry the loop, False to abort."""
    decision = ask_human("No matching slot — choose how to retry.")
    if decision["choice"] == "cancel":
        ctx.trace("Done", "user cancelled")
        return False
    changed = decision["params_changed"]
    if "time_window" in changed:
        ctx.time_window = changed["time_window"]
    if "cuisine" in changed:
        ctx.cuisine = changed["cuisine"]
    if "radius_m_increment" in changed:
        ctx.radius_m += changed["radius_m_increment"]
        ctx.cuisine = None
    ctx.attempt += 1
    ctx.trace("ask_human", f"retry with {changed}")
    return True


def _brief(r: dict) -> str:
    """Render a restaurant for the trace line. Adapts to whichever source
    populated the dict: OSM has distance_km; EazyDiner has rating + locality."""
    if r.get("source") == "eazydiner" or r.get("eazydiner_url"):
        bits = [r["name"]]
        if r.get("rating"):
            bits.append(f"★{r['rating']}")
        if r.get("locality"):
            bits.append(r["locality"])
        return "(".join([bits[0], ", ".join(bits[1:]) + ")"]) if len(bits) > 1 else bits[0]
    # Default (OSM): name(distance)
    return f"{r['name']}({r.get('distance_km', 0)}km)"


# --- Ollama planner (free local LLM, OpenAI-compatible) ----------------------
def run_ollama(ctx: Context) -> dict | None:
    """Uses Ollama's OpenAI-compatible /v1/chat/completions endpoint, which
    accepts OpenAI-shape tools and returns OpenAI-shape tool_calls.

    Routes through the shared http_client so retries + structured logging
    behave identically to provider calls. The 180s timeout is generous because
    local LLM inference for tool-call models can be slow on CPU-only setups.
    """
    s = get_settings()
    base = s.ollama_base_url.rstrip("/")
    url = f"{base}/v1/chat/completions"
    messages = _initial_messages(ctx)

    for i in range(MAX_ITERS):
        body = {"model": s.ollama_model, "messages": messages,
                "tools": TOOL_SCHEMAS, "tool_choice": "auto"}
        try:
            resp = http_request("POST", url, json=body, timeout=180)
        except requests.RequestException as e:
            ctx.trace("ollama", f"network error: {e}")
            return None
        if not resp.ok:
            ctx.trace("ollama", f"{resp.status_code}: {resp.text[:200]}")
            return None
        try:
            data = resp.json()
            msg = data["choices"][0]["message"]
        except (ValueError, KeyError, IndexError) as e:
            ctx.trace("ollama", f"unexpected response shape: {e} body={resp.text[:200]}")
            return None
        messages.append(msg)
        tool_calls = msg.get("tool_calls") or []
        if not tool_calls:
            ctx.trace("Done", (msg.get("content") or "")[:300])
            return {"final": msg.get("content", "")}
        for tc in tool_calls:
            fn = tc["function"]
            name = fn["name"]
            raw_args = fn["arguments"]
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
            except json.JSONDecodeError as e:
                ctx.trace(name, f"bad tool arguments JSON: {e}")
                args = {}
            ctx.trace(name, to_json(args)[:200])
            result = dispatch(name, args, ask_human)
            messages.append({
                "role": "tool",
                "tool_call_id": tc.get("id", name),
                "content": to_json(result),
            })
    ctx.trace("ollama", f"hit MAX_ITERS={MAX_ITERS}")
    return None


# --- Claude planner (Anthropic Messages, manual tool loop) --------------------
def run_claude(ctx: Context) -> dict | None:
    s = get_settings()
    if not s.anthropic_api_key:
        print("ANTHROPIC_API_KEY is not set. Add it to .env, or use --planner ollama/rule.")
        return None
    try:
        import anthropic  # type: ignore
    except ImportError:
        print("`pip install anthropic` and set ANTHROPIC_API_KEY, or use --planner ollama/rule.")
        return None
    client = anthropic.Anthropic(api_key=s.anthropic_api_key)
    user_msg = _initial_user_text(ctx)
    messages = [{"role": "user", "content": user_msg}]
    tools = schemas_anthropic()

    for _ in range(MAX_ITERS):
        resp = client.messages.create(
            model=s.anthropic_model, max_tokens=2048,
            system=SYSTEM_PROMPT, tools=tools, messages=messages)
        messages.append({"role": "assistant",
                         "content": _serialize_claude(resp.content)})
        if resp.stop_reason != "tool_use":
            text = "".join(getattr(b, "text", "") for b in resp.content if b.type == "text")
            ctx.trace("Done", text[:300])
            return {"final": text}
        tool_results = []
        for b in resp.content:
            if b.type == "tool_use":
                ctx.trace(b.name, to_json(b.input)[:200])
                out = dispatch(b.name, b.input, ask_human)
                tool_results.append({
                    "type": "tool_result", "tool_use_id": b.id,
                    "content": to_json(out),
                    **({"is_error": True} if out.get("is_error") else {}),
                })
        messages.append({"role": "user", "content": tool_results})
    ctx.trace("claude", f"hit MAX_ITERS={MAX_ITERS}")
    return None


def _serialize_claude(content) -> list:
    out = []
    for b in content:
        if b.type == "text":
            out.append({"type": "text", "text": b.text})
        elif b.type == "tool_use":
            out.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
    return out


# --- helpers ------------------------------------------------------------------
def _initial_user_text(ctx: Context) -> str:
    return (
        f"{ctx.query or 'Book a restaurant.'}\n"
        f"address: {ctx.address}\n"
        f"cuisine: {ctx.cuisine or '(any)'}\n"
        f"party: {ctx.party}\n"
        f"date: {ctx.date}\n"
        f"time_window: {ctx.time_window}\n"
        f"contact: {ctx.contact}\n"
    )


def _initial_messages(ctx: Context) -> list[dict]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": _initial_user_text(ctx)},
    ]
