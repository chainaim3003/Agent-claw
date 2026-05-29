"""Real external-service clients. Same module surface the agent dispatches against."""
from .exceptions import ProviderError, TransientProviderError, PermanentProviderError
from .geocode import get_user_location
from .search import search_restaurants
from .booking import check_availability, book_reservation, CalcomClient
from .sms import send_sms
from .calendar import create_calendar_event, setup_oauth as gcal_setup_oauth

__all__ = [
    "ProviderError", "TransientProviderError", "PermanentProviderError",
    "get_user_location", "search_restaurants",
    "check_availability", "book_reservation", "CalcomClient",
    "send_sms",
    "create_calendar_event", "gcal_setup_oauth",
]
