"""CLI entrypoint.

Examples:
  python -m agent_claw "italian dinner" --address "Bandra, Mumbai" \
      --party 2 --date 2026-06-01 --time 19:30 --contact +919876543210

  python -m agent_claw "dinner" --address "Andheri, Mumbai" --planner ollama
  python -m agent_claw "dinner" --address "Andheri, Mumbai" --planner claude
  python -m agent_claw --check     # validate .env / config; no API calls
"""
from __future__ import annotations
import argparse
import logging
import re
import sys
from datetime import date as _date, datetime

from . import logging_setup
from .agent.orchestrator import Context, run_rule, run_ollama, run_claude

log = logging.getLogger("cli")

# --- argparse `type=` validators ---------------------------------------------
_E164_RE = re.compile(r"^\+\d{7,15}$")


def _validate_date(s: str) -> str:
    try:
        datetime.strptime(s, "%Y-%m-%d")
    except ValueError as e:
        raise argparse.ArgumentTypeError(
            f"--date must be YYYY-MM-DD (got {s!r}): {e}")
    return s


def _validate_time(s: str) -> str:
    try:
        datetime.strptime(s, "%H:%M")
    except ValueError as e:
        raise argparse.ArgumentTypeError(
            f"--time must be HH:MM 24-hour (got {s!r}): {e}")
    return s


def _validate_contact(s: str) -> str:
    if not _E164_RE.match(s):
        raise argparse.ArgumentTypeError(
            f"--contact must be E.164 (e.g. +919876543210); got {s!r}")
    return s


def _validate_party(s: str) -> int:
    try:
        n = int(s)
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"--party must be an integer (got {s!r})")
    if not 1 <= n <= 20:
        raise argparse.ArgumentTypeError(f"--party must be between 1 and 20 (got {n})")
    return n


def _validate_positive_int(name: str):
    def check(s: str) -> int:
        try:
            n = int(s)
        except ValueError:
            raise argparse.ArgumentTypeError(f"--{name} must be a positive integer (got {s!r})")
        if n <= 0:
            raise argparse.ArgumentTypeError(f"--{name} must be > 0 (got {n})")
        return n
    return check


def _today_iso() -> str:
    return _date.today().isoformat()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="agent_claw",
                                description="Agent-claw — real CLI restaurant booking agent")
    p.add_argument("query", nargs="?", default="Book a restaurant",
                   help="free-text intent (used as label / LLM prompt seed)")
    p.add_argument("--planner", choices=["rule", "ollama", "claude"], default="rule",
                   help="rule = deterministic, ollama = local LLM, claude = Anthropic")
    # --address / --contact are NOT required at argparse level so --check works.
    # main() enforces them when a planner actually needs to run.
    p.add_argument("--address", help="location string (geocoded by Nominatim)")
    p.add_argument("--cuisine", default=None)
    p.add_argument("--party", type=_validate_party, default=2,
                   help="party size 1..20")
    p.add_argument("--date", type=_validate_date, default=_today_iso(),
                   help="YYYY-MM-DD (default: today)")
    p.add_argument("--time", type=_validate_time, default="19:30",
                   help="HH:MM 24-hour local time")
    p.add_argument("--contact", type=_validate_contact,
                   help="E.164 phone, e.g. +919876543210")
    p.add_argument("--nearest", type=_validate_positive_int("nearest"), default=5)
    p.add_argument("--radius-m", type=_validate_positive_int("radius-m"), default=3000)
    p.add_argument("--debug", action="store_true",
                   help="verbose logs + full tracebacks on error")
    p.add_argument("--check", action="store_true",
                   help="validate .env / configuration and exit (no API calls)")
    p.add_argument("--gcal-setup", action="store_true",
                   help="run Google Calendar OAuth consent flow and exit")
    return p


def build_context(a: argparse.Namespace) -> Context:
    return Context(
        query=a.query, address=a.address, cuisine=a.cuisine, party=a.party,
        date=a.date, time_window=a.time, contact=a.contact,
        nearest_n=a.nearest, radius_m=a.radius_m,
    )


def cmd_check() -> int:
    """Validate env / config without making external calls.

    Returns a shell exit code: 0 if all required vars are present, 1 otherwise.
    Twilio + Cal.com vars are flagged as REQUIRED; Anthropic is OPTIONAL
    (only needed for --planner claude).
    """
    from .config import get_settings
    try:
        s = get_settings()
    except Exception as e:  # noqa: BLE001
        print(f"Could not load settings: {e}", file=sys.stderr)
        return 1

    def _hide(v: str) -> str:
        return "<set>" if v else "<missing>"

    nominatim_ok = bool(s.nominatim_ua) and "set NOMINATIM_UA" not in s.nominatim_ua
    rows = [
        # (label, required?, ok?, displayed value)
        ("NOMINATIM_UA",         True,  nominatim_ok,                s.nominatim_ua),
        ("OVERPASS_URL",         True,  bool(s.overpass_url),        s.overpass_url),
        ("CALCOM_BASE_URL",      True,  bool(s.calcom_base_url),     s.calcom_base_url),
        ("CALCOM_API_KEY",       True,  bool(s.calcom_api_key),      _hide(s.calcom_api_key)),
        ("CALCOM_EVENT_TYPE_ID", True,  bool(s.calcom_event_type_id), s.calcom_event_type_id or "<missing>"),
        ("TWILIO_ACCOUNT_SID",   True,  bool(s.twilio_sid),          _hide(s.twilio_sid)),
        ("TWILIO_AUTH_TOKEN",    True,  bool(s.twilio_token),        _hide(s.twilio_token)),
        ("TWILIO_FROM_NUMBER",   True,  bool(s.twilio_from),         s.twilio_from or "<missing>"),
        ("TZ",                   True,  bool(s.timezone),            s.timezone),
        ("OLLAMA_BASE_URL",      False, True,                        s.ollama_base_url),
        ("OLLAMA_MODEL",         False, True,                        s.ollama_model),
        ("ANTHROPIC_API_KEY",    False, bool(s.anthropic_api_key),   _hide(s.anthropic_api_key)),
        ("GOOGLE_CLIENT_SECRETS_PATH", False, bool(s.google_client_secrets_path),
         s.google_client_secrets_path or "<not configured — calendar will be skipped>"),
        ("GOOGLE_CALENDAR_ID",   False, True,                        s.google_calendar_id),
        ("GOOGLE_REMINDER_MINUTES", False, True,                     s.google_reminder_minutes),
    ]
    print("=== Agent-claw configuration check ===")
    missing_required: list[str] = []
    for name, required, ok, val in rows:
        mark = "[OK]" if ok else ("[!! ]" if required else "[opt]")
        print(f"  {mark} {name:24s} {val}")
        if required and not ok:
            missing_required.append(name)
    print()
    if missing_required:
        print("Missing required env vars: " + ", ".join(missing_required), file=sys.stderr)
        print("Copy .env.example to .env and fill them in. See SETUP.md.", file=sys.stderr)
        return 1
    print("All required env vars present. No live API calls were made.")
    print("To smoke-test live services, run with --planner rule and real args.")
    return 0


def cmd_gcal_setup() -> int:
    """Run the Google Calendar OAuth consent flow. Opens a browser, captures
    user consent, writes the refresh token to GOOGLE_TOKEN_PATH (or
    data/google_token.json). Run this once after configuring
    GOOGLE_CLIENT_SECRETS_PATH in .env.
    """
    from .providers import gcal_setup_oauth
    try:
        path = gcal_setup_oauth()
    except Exception as e:  # noqa: BLE001 — we want a clean message either way
        print(f"Google Calendar OAuth setup failed: {e}", file=sys.stderr)
        return 1
    print(f"Google Calendar token saved to {path}")
    print("Future runs will refresh this token automatically.")
    return 0


def main() -> None:
    args = build_parser().parse_args()
    logging_setup.configure(logging.DEBUG if args.debug else logging.INFO)

    if args.check:
        sys.exit(cmd_check())

    if args.gcal_setup:
        sys.exit(cmd_gcal_setup())

    # Enforce required args here (not at argparse level) so --check works alone.
    if not args.address:
        print("error: --address is required (or use --check to validate env only)",
              file=sys.stderr)
        sys.exit(2)
    if not args.contact:
        print("error: --contact is required (or use --check to validate env only)",
              file=sys.stderr)
        sys.exit(2)

    ctx = build_context(args)

    print(f"=== agent-claw planner={args.planner} ===")
    runner = {"rule": run_rule, "ollama": run_ollama, "claude": run_claude}[args.planner]
    try:
        result = runner(ctx)
    except KeyboardInterrupt:
        print("\nInterrupted by user.", file=sys.stderr)
        sys.exit(130)
    except RuntimeError as e:
        # Missing/invalid env from `require()` in providers.
        print(f"\nConfiguration error: {e}", file=sys.stderr)
        if args.debug:
            raise
        sys.exit(1)
    except Exception as e:  # noqa: BLE001 — we want a clean message; --debug re-raises
        print(f"\nUnexpected error: {type(e).__name__}: {e}", file=sys.stderr)
        if args.debug:
            raise
        sys.exit(1)

    booked = bool(result and "confirmation_id" in str(result))
    print("=== result:", "booked" if booked else "see log above", "===")
    sys.exit(0 if booked else 1)


if __name__ == "__main__":
    main()
