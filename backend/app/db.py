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

            CREATE TABLE IF NOT EXISTS chores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                frequency TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS preferences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                roommate_id INTEGER NOT NULL REFERENCES roommates(id),
                chore_id INTEGER NOT NULL REFERENCES chores(id),
                week_start TEXT NOT NULL,
                wtp_cents INTEGER NOT NULL DEFAULT 0,
                bid_cents INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(roommate_id, chore_id, week_start)
            );

            CREATE TABLE IF NOT EXISTS ledger_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                week_start TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS ledger_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL REFERENCES ledger_runs(id) ON DELETE CASCADE,
                week_start TEXT NOT NULL,
                chore_id INTEGER NOT NULL REFERENCES chores(id),
                assignee_id INTEGER REFERENCES roommates(id),
                surplus_cents INTEGER NOT NULL DEFAULT 0,
                notes TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS ledger_payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_id INTEGER NOT NULL REFERENCES ledger_entries(id) ON DELETE CASCADE,
                roommate_id INTEGER NOT NULL REFERENCES roommates(id),
                amount_cents INTEGER NOT NULL
            );
            """
        )
        seed_chores(conn)
        seed_roommates(conn)


def seed_chores(conn: sqlite3.Connection) -> None:
    count = conn.execute("SELECT COUNT(*) FROM chores").fetchone()[0]
    if count:
        return

    conn.executemany(
        """
        INSERT INTO chores (name, frequency, description)
        VALUES (?, ?, ?)
        """,
        [
            ("Dishes", "weekly", "Kitchen reset, dishes, and counters"),
            ("Trash", "weekly", "Take out trash and recycling"),
            ("Bathroom", "monthly", "Clean sink, toilet, shower, and floor"),
            ("Vacuum", "weekly", "Vacuum shared floors and rugs"),
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
