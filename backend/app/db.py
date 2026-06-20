from __future__ import annotations

import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "choremarket.sqlite3"


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS roommates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            -- Templates. Each active recurring chore spawns one instance per week.
            CREATE TABLE IF NOT EXISTS recurring_chores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT NOT NULL DEFAULT '',
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
            CREATE TABLE IF NOT EXISTS chore_instances (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recurring_chore_id INTEGER REFERENCES recurring_chores(id),
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                week_start TEXT NOT NULL,
                due_date TEXT NOT NULL,
                assignee_id INTEGER REFERENCES roommates(id),
                status TEXT NOT NULL DEFAULT 'pending',
                surplus_cents INTEGER NOT NULL DEFAULT 0,
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(recurring_chore_id, week_start)
            );

            CREATE TABLE IF NOT EXISTS instance_payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                instance_id INTEGER NOT NULL
                    REFERENCES chore_instances(id) ON DELETE CASCADE,
                roommate_id INTEGER NOT NULL REFERENCES roommates(id),
                amount_cents INTEGER NOT NULL
            );
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
