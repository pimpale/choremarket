from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from .mock_data import MOCK_ROOMMATES


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

            -- Append-only WTP/bid edit log keyed by recurring chore. Every edit
            -- inserts a new row stamped with when it was made (created_at); the
            -- value in force for a given week is the most recent row whose edit
            -- landed on or before the end of that week. So editing a bid changes
            -- the current/future weeks but never rewrites settled past weeks, and
            -- the full history is preserved. wtp/bid are nullable: an unset value
            -- means "no preference", which the client treats as $0 WTP and a very
            -- large bid (so an un-bid roommate is never auto-assigned), exactly
            -- like one-offs.
            CREATE TABLE IF NOT EXISTS chore_preferences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                roommate_id INTEGER NOT NULL REFERENCES roommates(id),
                recurring_chore_id INTEGER NOT NULL REFERENCES recurring_chores(id),
                wtp_cents INTEGER,
                bid_cents INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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

            INSERT OR IGNORE INTO app_settings (key, value) VALUES ('mechanism', 'first-best');
            """
        )
        _migrate_chore_preferences(conn)
        seed_recurring_chores(conn)
        seed_roommates(conn)


# Older databases (e.g. a Railway volume) still have the week-independent
# single-value schema (a UNIQUE (roommate, chore) row with updated_at, no
# created_at). ``CREATE TABLE IF NOT EXISTS`` won't alter them, so rebuild the
# table into the append-only edit-log shape, backdating every existing preference
# to the beginning of time so it keeps applying to all past weeks.
def _migrate_chore_preferences(conn: sqlite3.Connection) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(chore_preferences)")}
    if not columns or "created_at" in columns:
        return
    conn.executescript(
        """
        ALTER TABLE chore_preferences RENAME TO chore_preferences_old;
        CREATE TABLE chore_preferences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            roommate_id INTEGER NOT NULL REFERENCES roommates(id),
            recurring_chore_id INTEGER NOT NULL REFERENCES recurring_chores(id),
            wtp_cents INTEGER,
            bid_cents INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO chore_preferences
            (id, roommate_id, recurring_chore_id, wtp_cents, bid_cents, created_at)
        SELECT id, roommate_id, recurring_chore_id, wtp_cents, bid_cents, '1970-01-01 00:00:00'
        FROM chore_preferences_old;
        DROP TABLE chore_preferences_old;
        """
    )


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
        [(name,) for name in MOCK_ROOMMATES],
    )
