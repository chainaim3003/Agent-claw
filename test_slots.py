"""Smoke test 3: Cal.com availability check.

Run from Restaurant-claw/ with venv active:
    python test_slots.py

Expected output: a non-empty list of ISO timestamps like
    ['2026-06-02T14:00:00.000Z', '2026-06-02T14:30:00.000Z', ...]

If the list is EMPTY:
  - Your Cal.com event type has no availability around 19:30 on that date.
  - Fix: Cal.com web UI -> your event type -> Availability tab ->
    set working hours to cover 19:30 (e.g. 09:00 - 22:00).

If it ERRORS with 401 / 404:
  - CALCOM_API_KEY or CALCOM_EVENT_TYPE_ID in .env is wrong.
"""
from agent_claw.providers import check_availability

# Pick a date a few days in the future (today is 2026-05-29).
DATE = "2026-06-02"
TIME = "19:30"
PARTY = 2

restaurant = {"id": "test-stub", "name": "test"}
slots = check_availability(restaurant, DATE, TIME, PARTY)

print(f"Slots for {DATE} around {TIME} (party={PARTY}):")
if not slots:
    print("  <empty>  -> see header comment in this file for fixes")
else:
    for s in slots:
        print(f"  {s}")
    print(f"\nTotal: {len(slots)} slot(s).")
