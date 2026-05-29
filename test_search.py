"""Smoke test 2: Overpass restaurant search near Coimbatore.

Run from Restaurant-claw/ with venv active:
    python test_search.py

Expected output: up to 5 lines, each like
    Annapoorna 0.42 km
If empty, the radius is widened automatically and retried at 8000m.
"""
from agent_claw.providers import search_restaurants

# Coimbatore city centre coordinates.
LAT, LNG = 11.0168, 76.9558

results = search_restaurants(LAT, LNG, limit=5)
if not results:
    print("No results within 3000m; retrying at 8000m radius...")
    results = search_restaurants(LAT, LNG, limit=5, radius_m=8000)

print(f"Found {len(results)} restaurant(s):")
for x in results:
    print(f"  {x['name']:40s} {x['distance_km']} km   (id={x['id']})")
