"""Tool schemas (OpenAI/Anthropic-compatible JSON Schema) + dispatcher.

The Ollama planner uses OpenAI-style schemas. The Claude planner needs the
inner `function` object as the top-level schema (no `type: function` wrapper).
Both helper functions below produce the right shape from the canonical list."""
from __future__ import annotations
import json

from ..providers import (get_user_location, search_restaurants,
                         check_availability, book_reservation, send_sms,
                         send_email,
                         create_calendar_event,
                         ProviderError)
from ..storage import save_booking
from ..logging_setup import get_logger

log = get_logger("agent.tools")

# Canonical tool definitions (OpenAI shape; we translate to Anthropic on demand).
TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_user_location",
            "description": "Geocode an address string to {lat, lng, label}. Address is required.",
            "parameters": {
                "type": "object",
                "properties": {"address": {"type": "string"}},
                "required": ["address"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_restaurants",
            "description": "Find named restaurants near (lat,lng), filtered by cuisine, sorted by distance.",
            "parameters": {
                "type": "object",
                "properties": {
                    "lat": {"type": "number"},
                    "lng": {"type": "number"},
                    "cuisine": {"type": "string"},
                    "limit": {"type": "integer"},
                    "radius_m": {"type": "integer"},
                },
                "required": ["lat", "lng"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_availability",
            "description": "Real bookable slots at a Cal.com event type for a date/time. Returns a list of ISO start strings.",
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "restaurant_name": {"type": "string"},
                    "date": {"type": "string", "description": "YYYY-MM-DD"},
                    "time_window": {"type": "string", "description": "HH:MM local"},
                    "party": {"type": "integer"},
                },
                "required": ["restaurant_id", "restaurant_name", "date", "time_window", "party"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ask_human",
            "description": "Pause and ask the user how to proceed when no slot is found. Returns their choice and any new params.",
            "parameters": {
                "type": "object",
                "properties": {"reason": {"type": "string"}},
                "required": ["reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "book_reservation",
            "description": "Create a REAL booking on Cal.com. Idempotent on (date,slot,attendee). Returns confirmation_id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "restaurant_name": {"type": "string"},
                    "date": {"type": "string"},
                    "slot": {"type": "string", "description": "ISO start from check_availability"},
                    "party": {"type": "integer"},
                    "contact": {"type": "string", "description": "E.164 phone"},
                },
                "required": ["restaurant_id", "restaurant_name", "date", "slot", "party", "contact"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_sms",
            "description": "Send the booking confirmation via Twilio SMS.",
            "parameters": {
                "type": "object",
                "properties": {"to": {"type": "string"}, "body": {"type": "string"}},
                "required": ["to", "body"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_email",
            "description": (
                "Send a booking confirmation email via Gmail SMTP. Use once for "
                "the customer and once for the restaurant."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {"type": "string", "description": "recipient email"},
                    "subject": {"type": "string"},
                    "body": {"type": "string"},
                },
                "required": ["to", "subject", "body"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_booking",
            "description": "Persist the booking + generate PDF invoice. Returns row_id and invoice_path.",
            "parameters": {
                "type": "object",
                "properties": {"booking": {"type": "object"}},
                "required": ["booking"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_calendar_event",
            "description": (
                "Create a Google Calendar event for the confirmed booking, with "
                "reminder notifications. Returns event_id + html_link, or "
                "{skipped:true} if Google integration isn't configured."
            ),
            "parameters": {
                "type": "object",
                "properties": {"booking": {"type": "object"}},
                "required": ["booking"],
            },
        },
    },
]


def schemas_anthropic() -> list[dict]:
    """Anthropic uses {name, description, input_schema} (no `function` wrapper)."""
    out = []
    for t in TOOL_SCHEMAS:
        fn = t["function"]
        out.append({"name": fn["name"], "description": fn["description"],
                    "input_schema": fn["parameters"]})
    return out


# --- dispatch -----------------------------------------------------------------
def dispatch(name: str, args: dict, ask_human_fn) -> dict:
    """Execute a tool by name. Errors are wrapped so the caller can choose
    whether to surface them to the LLM as tool_result(is_error=True) or to
    raise to the user."""
    try:
        if name == "get_user_location":
            return get_user_location(args["address"])
        if name == "search_restaurants":
            return {"results": search_restaurants(
                args["lat"], args["lng"],
                cuisine=args.get("cuisine"),
                limit=args.get("limit", 5),
                radius_m=args.get("radius_m", 3000))}
        if name == "check_availability":
            restaurant = {"id": args["restaurant_id"], "name": args["restaurant_name"]}
            slots = check_availability(restaurant, args["date"],
                                       args["time_window"], args["party"])
            return {"restaurant_id": restaurant["id"], "slots": slots}
        if name == "ask_human":
            return ask_human_fn(args.get("reason", "no slot found"))
        if name == "book_reservation":
            restaurant = {"id": args["restaurant_id"], "name": args["restaurant_name"]}
            return book_reservation(restaurant, args["date"], args["slot"],
                                    args["party"], args["contact"])
        if name == "send_sms":
            return send_sms(args["to"], args["body"])
        if name == "send_email":
            return send_email(args["to"], args["subject"], args["body"])
        if name == "save_booking":
            return save_booking(args["booking"])
        if name == "create_calendar_event":
            return create_calendar_event(args["booking"])
        return {"error": f"unknown tool {name!r}"}
    except ProviderError as e:
        log.warning("tool %s provider error: %s", name, e)
        return {"error": str(e), "is_error": True}


def to_json(obj) -> str:
    return json.dumps(obj, default=str)
