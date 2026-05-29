"""Agent-claw — a real, end-to-end CLI restaurant-booking agent.

Wires real external services with no mocks:
  geocoding  -> Nominatim (OSM)
  search     -> Overpass (OSM)
  booking    -> Cal.com v2 (cloud or self-hosted)
  sms        -> Twilio Programmable Messaging
  brain      -> deterministic rules, local Ollama (free), or Anthropic Claude

Diagram nodes -> modules:
  User intent ............ cli.py
  get_user_location ...... providers/geocode.py     (Nominatim /search)
  search_restaurants ..... providers/search.py      (Overpass interpreter)
  check_availability ..... providers/booking.py     (Cal.com GET /v2/slots)
  Slot found? / ask_human  agent/orchestrator.py
  book_reservation ....... providers/booking.py     (Cal.com POST /v2/bookings)
  send_sms ............... providers/sms.py         (Twilio Messages)
  save_booking ........... storage/db.py + storage/invoice.py
"""
__version__ = "1.0.0"
