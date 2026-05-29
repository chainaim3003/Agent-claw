"""Smoke test 1: Nominatim geocoding.

Run from Restaurant-claw/ with venv active:
    python test_geocode.py

Expected output:
    {'lat': 11.01..., 'lng': 76.95..., 'label': 'Coimbatore, ...'}
"""
from agent_claw.providers import get_user_location

result = get_user_location("Coimbatore, Tamil Nadu")
print(result)
