# Agent-claw

Real, end-to-end CLI restaurant-booking agent. No mocks. Every external call
hits a live service; every "confirmed" is a row in a real reservation system.

```
User intent ──► get_user_location ──► search_restaurants ──► check_availability ──┐
                  (Nominatim)             (Overpass)              (Cal.com /slots)  │
                                                                                    ▼
                                                                          Slot found?
                                                                          /        \
                                                                         no         yes
                                                                         │           │
                                                                    ask_human        │
                                                                  (retry with        ▼
                                                                   new params) ◄── book_reservation
                                                                                     (Cal.com /bookings)
                                                                                     │
                                                                                     ▼
                                                                                  send_sms (Twilio)
                                                                                     │
                                                                                     ▼
                                                                              save_booking
                                                                            (SQLite + PDF)
                                                                                     │
                                                                                     ▼
                                                                                   Done
```

## Project layout

```
Agent-claw/                     parent umbrella (room for sibling agents)
└── Restaurant-claw/            this project
    ├── README.md               this file
    ├── SETUP.md                step-by-step setup (Windows-friendly)
    ├── requirements.txt
    ├── .env.example            every env var documented
    ├── .gitignore
    └── agent_claw/             the importable Python package
        ├── __init__.py
        ├── __main__.py         python -m agent_claw works
        ├── config.py           Settings dataclass; validates env on use
        ├── logging_setup.py    single configure() + get_logger()
        ├── http_client.py      shared requests.Session with retries + timeouts
        ├── providers/
        │   ├── __init__.py
        │   ├── exceptions.py   Transient / Permanent provider errors
        │   ├── geocode.py      Nominatim (1 req/s, cached, identifying UA)
        │   ├── search.py       Overpass amenity=restaurant
        │   ├── booking.py      Cal.com v2 client + tool-shape wrappers
        │   └── sms.py          Twilio Programmable Messaging
        ├── storage/
        │   ├── __init__.py
        │   ├── db.py           SQLite (UNIQUE confirmation_id → idempotent)
        │   └── invoice.py      reportlab PDF (text fallback)
        ├── agent/
        │   ├── __init__.py
        │   ├── prompts.py      system prompt
        │   ├── tools.py        JSON-Schema tool defs + dispatch()
        │   └── orchestrator.py run_rule, run_ollama, run_claude, ask_human
        └── cli.py              argparse entrypoint
```

Run all commands from `Agent-claw\Restaurant-claw\` (where this README lives).

## How modules connect

- **`cli.py`** parses flags → builds a `Context` → picks a planner.
- The planner lives in **`agent/orchestrator.py`**. Three are available:
  - `run_rule` — deterministic state machine walking the diagram exactly. No
    LLM, no API key. Best for validation, the safe default when no model is set up.
  - `run_ollama` — POSTs to `OLLAMA_BASE_URL/v1/chat/completions` (OpenAI-
    compatible). Free local LLM. Model picks tool calls; we execute them.
  - `run_claude` — Anthropic Messages API manual tool-use loop.
- Each planner calls **`agent/tools.dispatch(name, args, ask_human)`** for every
  tool invocation. The dispatcher maps names → real provider functions, wraps
  `ProviderError` so transient failures surface as a tool_result with
  `is_error: true` (the LLM can retry; the rule planner aborts that step).
- Provider modules in **`providers/`** are the *only* code that talks to
  external services. Each uses **`http_client.request()`**, which carries a
  `requests.Session` configured with retry on `[429, 500, 502, 503, 504]`,
  connect/read timeouts, and logging.
- **`storage/db.py`** persists the booking; **`storage/invoice.py`** writes the
  PDF. Both are called from a single `save_booking` so the agent does one tool
  call.

## Three real-time properties worth knowing

1. **Idempotency.** Cal.com refuses to double-book a slot at the API layer. On
   our side, the SQLite `UNIQUE(confirmation_id)` plus `INSERT OR IGNORE` mean
   a retried `save_booking` does not create a second row.
2. **Retries.** `http_client.py` retries transient failures (429 + 5xx) with
   exponential backoff. 4xx (other than 429) is permanent and surfaces as a
   `PermanentProviderError` — never silently retried, because that hides bad
   inputs (wrong number, missing required Cal.com field, etc.).
3. **Determinism toggle.** Re-running with `--planner rule` walks the same path
   given the same inputs — useful for debugging the *system* without LLM
   non-determinism in the loop.

## Running

See **SETUP.md** for the step-by-step. Quick form:

```
python -m agent_claw "italian dinner" \
    --address "Bandra, Mumbai" \
    --party 2 --date 2026-06-01 --time 19:30 \
    --contact +919876543210 \
    --planner rule
```

## What is real vs. what is configured

- **Real, every run:** geocoding (Nominatim), restaurant discovery (Overpass),
  booking + double-book prevention (Cal.com), SMS (Twilio), persistence (SQLite),
  PDF invoice (reportlab).
- **You provide:** a Cal.com event type representing the bookable venue, a
  Twilio number (and verified recipient if on trial), and either an Ollama
  install or an Anthropic API key (or use `--planner rule`, no model needed).

The booked venue is genuinely reserved in a real Cal.com calendar; the
*restaurant name shown in the invoice* comes from OSM. Because no free
real-world restaurant booking API exists for arbitrary third parties, the
Cal.com event type stands in as the real reservation system for the venue(s)
you operate. This is documented in `SETUP.md`.
