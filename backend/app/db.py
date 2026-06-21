from __future__ import annotations

import os
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
# Override with CHOREMARKET_DB to point at a persistent volume in production
# (e.g. a Railway volume mounted at /data).
DB_PATH = Path(os.environ.get("CHOREMARKET_DB", str(ROOT / "choremarket.sqlite3")))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(
            """
            -- join_date/leave_date bound a roommate's membership window. A
            -- roommate only participates in chores whose week falls within it;
            -- NULL means open-ended (always a member on that side). "Removing" a
            -- roommate just sets leave_date. ``active`` is a convenience mirror of
            -- "currently a member" used for default listings.
            CREATE TABLE IF NOT EXISTS roommates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                active INTEGER NOT NULL DEFAULT 1,
                join_date TEXT,
                leave_date TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            -- Recorded settle-up payments between roommates (A pays B). These
            -- adjust the net balance without touching the house/chore ledger.
            CREATE TABLE IF NOT EXISTS roommate_payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                from_roommate_id INTEGER NOT NULL REFERENCES roommates(id),
                to_roommate_id INTEGER NOT NULL REFERENCES roommates(id),
                amount_cents INTEGER NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                paid_on TEXT NOT NULL DEFAULT (date('now')),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            -- Templates. Cadence controls auto-spawning:
            -- weekly = every week, monthly = week containing the 1st, ad-hoc =
            -- template only.
            CREATE TABLE IF NOT EXISTS recurring_chores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT NOT NULL DEFAULT '',
                cadence TEXT NOT NULL DEFAULT 'weekly',
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            -- Week-independent WTP/bid preferences keyed by recurring chore.
            CREATE TABLE IF NOT EXISTS chore_preferences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                roommate_id INTEGER NOT NULL REFERENCES roommates(id),
                recurring_chore_id INTEGER NOT NULL REFERENCES recurring_chores(id),
                wtp_cents INTEGER NOT NULL DEFAULT 0,
                bid_cents INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(roommate_id, recurring_chore_id)
            );

            -- The ledger rows: one concrete instance of a chore for a given week.
            -- recurring_chore_id is NULL for one-offs entered directly on the ledger.
            -- Only raw primitives are stored: assignee_id is the one-off / manual
            -- override (NULL = auto for recurring), payout_cents is a one-off's
            -- payout. Who does each recurring chore and every transfer are derived
            -- on the client from the current preferences and active mechanism.
            CREATE TABLE IF NOT EXISTS chore_instances (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recurring_chore_id INTEGER REFERENCES recurring_chores(id),
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                week_start TEXT NOT NULL,
                due_date TEXT NOT NULL,
                assignee_id INTEGER REFERENCES roommates(id),
                status TEXT NOT NULL DEFAULT 'pending',
                payout_cents INTEGER NOT NULL DEFAULT 0,
                manual_override INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            -- Per-instance WTP/bid overrides for one-off ledger rows. NULL
            -- means visually unset; the client treats unset one-off WTP as $0
            -- and unset one-off bid as a very large price.
            CREATE TABLE IF NOT EXISTS chore_instance_preferences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chore_instance_id INTEGER NOT NULL REFERENCES chore_instances(id) ON DELETE CASCADE,
                roommate_id INTEGER NOT NULL REFERENCES roommates(id),
                wtp_cents INTEGER,
                bid_cents INTEGER,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(chore_instance_id, roommate_id)
            );

            -- Global key/value settings (e.g. the active transfer mechanism).
            -- Kept here so the chore tables stay mechanism-agnostic; all
            -- mechanism-specific numbers are recomputed from primitives.
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            INSERT OR IGNORE INTO app_settings (key, value) VALUES ('mechanism', 'agv');
            """
        )
        seed_recurring_chores(conn)
        seed_roommates(conn)


def seed_recurring_chores(conn: sqlite3.Connection) -> None:
    count = conn.execute("SELECT COUNT(*) FROM recurring_chores").fetchone()[0]
    if count:
        return

    conn.executemany(
        """
        INSERT INTO recurring_chores (name, description)
        VALUES (?, ?)
        """,
        [
            ("Dishes", "Kitchen reset, dishes, and counters"),
            ("Trash", "Take out trash and recycling"),
            ("Bathroom", "Clean sink, toilet, shower, and floor"),
            ("Vacuum", "Vacuum shared floors and rugs"),
        ],
    )


def seed_roommates(conn: sqlite3.Connection) -> None:
    count = conn.execute("SELECT COUNT(*) FROM roommates").fetchone()[0]
    if count:
        return

    conn.executemany(
        "INSERT INTO roommates (name) VALUES (?)",
        [
            ("Alex",),
            ("Blair",),
            ("Casey",),
        ],
    )
