"""save_booking -> SQLite. UNIQUE(confirmation_id) guarantees idempotency.

For production scale move to Postgres (same schema, change DSN). The agent's
flow never relies on database-specific features."""
from __future__ import annotations
import sqlite3
from datetime import datetime, timezone

from ..config import get_settings, DB_PATH
from ..logging_setup import get_logger
from .invoice import generate_invoice

log = get_logger("storage.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS bookings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    confirmation_id TEXT UNIQUE NOT NULL,
    restaurant_id TEXT NOT NULL,
    restaurant_name TEXT NOT NULL,
    date TEXT NOT NULL,
    slot TEXT NOT NULL,
    party INTEGER NOT NULL,
    contact TEXT NOT NULL,
    status TEXT NOT NULL,
    provider TEXT NOT NULL,
    invoice_path TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bookings_created_at ON bookings(created_at);
"""


def _connect() -> sqlite3.Connection:
    get_settings()  # ensures DATA_DIR exists
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init_db() -> None:
    with _connect() as c:
        c.executescript(_SCHEMA)
    log.debug("sqlite ready at %s", DB_PATH)


def save_booking(record: dict, **_: object) -> dict:
    """Persist + generate invoice. Idempotent on confirmation_id."""
    init_db()
    invoice_path = generate_invoice(record)
    created_at = datetime.now(timezone.utc).isoformat()
    with _connect() as c:
        c.execute(
            """INSERT OR IGNORE INTO bookings
               (confirmation_id, restaurant_id, restaurant_name, date, slot,
                party, contact, status, provider, invoice_path, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (record["confirmation_id"], record["restaurant_id"],
             record["restaurant_name"], record["date"], record["slot"],
             record["party"], record["contact"], record["status"],
             record["provider"], str(invoice_path), created_at),
        )
        row = c.execute(
            "SELECT id, invoice_path FROM bookings WHERE confirmation_id=?",
            (record["confirmation_id"],)).fetchone()
    log.info("saved booking row=%s confirmation=%s", row["id"], record["confirmation_id"])
    return {"row_id": row["id"], "invoice_path": row["invoice_path"]}
