from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from .db import connect
from .weeks import current_week, due_date_for, upcoming_week


VALID_STATUSES = {"pending", "done", "failed"}
MECHANISMS = {"agv", "vcg"}
CADENCES = {"weekly", "monthly", "ad-hoc"}
EXAMPLE_ROOMMATES = ("Alex", "Blair", "Casey")

MOCK_ROOMMATES = ("Matthew", "Govind", "Blaine", "Emerson", "Nathan")
MOCK_RECURRING_CHORES = [
    ("Monday Trash", "Take out Monday trash"),
    ("Thursday Trash + Recycle", "Take out Thursday trash and recycling"),
    ("Putting away dishes", "Empty and reload the dishwasher as needed"),
    ("Kitchen surfaces", "Wipe counters, stove, and dining table"),
    ("Vacuum/sweep downstairs", "Vacuum and sweep downstairs and the stairs"),
    ("Clean kitchen sink", "Scrub and clean the kitchen sink"),
    ("Clean microwave", "Wipe down the microwave inside and out"),
]


# --------------------------------------------------------------------------- #
# Settings (the active transfer mechanism)
# --------------------------------------------------------------------------- #
def get_setting(key: str, default: str | None = None) -> str | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO app_settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )


def get_mechanism() -> str:
    value = get_setting("mechanism", "agv")
    return value if value in MECHANISMS else "agv"


def set_mechanism(value: str) -> str:
    """Persist the active mechanism. The transfers themselves are derived on the
    client, so there is nothing to recompute here."""
    if value not in MECHANISMS:
        raise ValueError(f"Unknown mechanism: {value!r}")
    set_setting("mechanism", value)
    return value


# --------------------------------------------------------------------------- #
# Roommates
# --------------------------------------------------------------------------- #
def active_roommates() -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM roommates WHERE active = 1 ORDER BY name"
        ).fetchall()


def all_roommates() -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM roommates ORDER BY active DESC, name"
        ).fetchall()


def add_roommate(name: str, join_date: str | None = None) -> None:
    clean = name.strip()
    if not clean:
        return
    join = (join_date or date.today().isoformat())
    with connect() as conn:
        existing = conn.execute(
            "SELECT id FROM roommates WHERE name = ?", (clean,)
        ).fetchone()
        if existing:
            # Re-joining: reopen membership from today (keep the original join_date).
            conn.execute(
                "UPDATE roommates SET active = 1, leave_date = NULL WHERE id = ?",
                (existing["id"],),
            )
        else:
            conn.execute(
                "INSERT INTO roommates (name, join_date) VALUES (?, ?)",
                (clean, join),
            )


def add_example_roommates() -> None:
    for name in EXAMPLE_ROOMMATES:
        add_roommate(name)


def remove_roommate(roommate_id: int, leave_date: str | None = None) -> None:
    """'Removing' a roommate just closes their membership window."""
    leave = leave_date or date.today().isoformat()
    with connect() as conn:
        conn.execute(
            "UPDATE roommates SET active = 0, leave_date = ? WHERE id = ?",
            (leave, roommate_id),
        )


def update_roommate_dates(
    roommate_id: int,
    join_date: str | None,
    leave_date: str | None,
) -> None:
    """Edit a roommate's membership window. ``active`` mirrors whether their
    window is currently open as of today."""
    today = date.today().isoformat()
    is_active = 1 if (leave_date is None or leave_date >= today) else 0
    with connect() as conn:
        conn.execute(
            "UPDATE roommates SET join_date = ?, leave_date = ?, active = ? WHERE id = ?",
            (join_date or None, leave_date or None, is_active, roommate_id),
        )


# --------------------------------------------------------------------------- #
# Recurring chores (templates)
# --------------------------------------------------------------------------- #
def active_recurring_chores() -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM recurring_chores WHERE active = 1 ORDER BY cadence, name"
        ).fetchall()


def _clean_cadence(cadence: str) -> str:
    clean = cadence.strip().lower()
    if clean not in CADENCES:
        raise ValueError(f"Unknown cadence: {cadence!r}")
    return clean


def add_recurring_chore(name: str, description: str, cadence: str = "weekly") -> None:
    clean_name = name.strip()
    clean_description = description.strip()
    clean_cadence = _clean_cadence(cadence)
    if not clean_name:
        return
    with connect() as conn:
        existing = conn.execute(
            "SELECT id FROM recurring_chores WHERE name = ?", (clean_name,)
        ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE recurring_chores
                SET description = ?, cadence = ?, active = 1
                WHERE id = ?
                """,
                (clean_description, clean_cadence, existing["id"]),
            )
        else:
            conn.execute(
                """
                INSERT INTO recurring_chores (name, description, cadence)
                VALUES (?, ?, ?)
                """,
                (clean_name, clean_description, clean_cadence),
            )


def update_recurring_chore(
    chore_id: int,
    name: str,
    description: str,
    cadence: str = "weekly",
) -> None:
    clean_name = name.strip()
    clean_description = description.strip()
    clean_cadence = _clean_cadence(cadence)
    if not clean_name:
        return
    with connect() as conn:
        duplicate = conn.execute(
            "SELECT id FROM recurring_chores WHERE name = ? AND id != ?",
            (clean_name, chore_id),
        ).fetchone()
        if duplicate:
            return
        conn.execute(
            """
            UPDATE recurring_chores
            SET name = ?, description = ?, cadence = ?, active = 1
            WHERE id = ?
            """,
            (clean_name, clean_description, clean_cadence, chore_id),
        )


def remove_recurring_chore(chore_id: int) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE recurring_chores SET active = 0 WHERE id = ?", (chore_id,)
        )


# --------------------------------------------------------------------------- #
# Preferences (week-independent wtp/bid keyed by recurring chore)
# --------------------------------------------------------------------------- #
def save_preference(
    roommate_id: int,
    recurring_chore_id: int,
    wtp_cents: int,
    bid_cents: int,
) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO chore_preferences
                (roommate_id, recurring_chore_id, wtp_cents, bid_cents)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(roommate_id, recurring_chore_id)
            DO UPDATE SET
                wtp_cents = excluded.wtp_cents,
                bid_cents = excluded.bid_cents,
                updated_at = CURRENT_TIMESTAMP
            """,
            (roommate_id, recurring_chore_id, wtp_cents, bid_cents),
        )


def preferences_by_chore() -> dict[int, dict[int, dict[str, int]]]:
    """{recurring_chore_id: {roommate_id: {wtp_cents, bid_cents}}}.

    Covers every roommate (not just current members) so the client can recompute
    historical weeks for people who have since left; the client filters who
    actually participates in each week by membership dates.
    """
    roommates = all_roommates()
    chores = active_recurring_chores()
    out: dict[int, dict[int, dict[str, int]]] = {}
    with connect() as conn:
        for chore in chores:
            out[chore["id"]] = {}
            for roommate in roommates:
                row = conn.execute(
                    """
                    SELECT wtp_cents, bid_cents
                    FROM chore_preferences
                    WHERE roommate_id = ? AND recurring_chore_id = ?
                    """,
                    (roommate["id"], chore["id"]),
                ).fetchone()
                out[chore["id"]][roommate["id"]] = {
                    "wtp_cents": row["wtp_cents"] if row else 0,
                    "bid_cents": row["bid_cents"] if row else 0,
                }
    return out


def save_instance_preference(
    chore_instance_id: int,
    roommate_id: int,
    wtp_cents: int | None,
    bid_cents: int | None,
) -> None:
    with connect() as conn:
        instance = conn.execute(
            """
            SELECT recurring_chore_id
            FROM chore_instances
            WHERE id = ?
            """,
            (chore_instance_id,),
        ).fetchone()
        if not instance:
            raise ValueError(f"Unknown instance: {chore_instance_id}")
        if instance["recurring_chore_id"] is not None:
            raise ValueError("Instance preferences are only editable for one-offs")

        roommate = conn.execute(
            "SELECT id FROM roommates WHERE id = ? AND active = 1",
            (roommate_id,),
        ).fetchone()
        if not roommate:
            raise ValueError(f"Unknown roommate: {roommate_id}")

        conn.execute(
            """
            INSERT INTO chore_instance_preferences
                (chore_instance_id, roommate_id, wtp_cents, bid_cents)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(chore_instance_id, roommate_id)
            DO UPDATE SET
                wtp_cents = excluded.wtp_cents,
                bid_cents = excluded.bid_cents,
                updated_at = CURRENT_TIMESTAMP
            """,
            (chore_instance_id, roommate_id, wtp_cents, bid_cents),
        )


def preferences_by_instance() -> dict[int, dict[int, dict[str, int | None]]]:
    """{chore_instance_id: {roommate_id: {wtp_cents, bid_cents}}} for one-offs.

    Missing values are returned as NULL so the client can render them blank while
    still using calculation defaults.
    """
    roommates = all_roommates()
    with connect() as conn:
        instances = conn.execute(
            """
            SELECT id
            FROM chore_instances
            WHERE recurring_chore_id IS NULL
            """
        ).fetchall()
        out: dict[int, dict[int, dict[str, int | None]]] = {}
        for instance in instances:
            out[instance["id"]] = {}
            for roommate in roommates:
                row = conn.execute(
                    """
                    SELECT wtp_cents, bid_cents
                    FROM chore_instance_preferences
                    WHERE chore_instance_id = ? AND roommate_id = ?
                    """,
                    (instance["id"], roommate["id"]),
                ).fetchone()
                out[instance["id"]][roommate["id"]] = {
                    "wtp_cents": row["wtp_cents"] if row else None,
                    "bid_cents": row["bid_cents"] if row else None,
                }
    return out


# --------------------------------------------------------------------------- #
# Instances (the ledger rows) + spawning
# --------------------------------------------------------------------------- #
def _insert_instance(
    conn: sqlite3.Connection,
    recurring_chore_id: int | None,
    name: str,
    description: str,
    week_start: str,
    due_date: str,
    assignee_id: int | None,
    status: str,
    payout_cents: int,
    manual_override: bool = False,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO chore_instances
            (recurring_chore_id, name, description, week_start, due_date,
             assignee_id, status, payout_cents, manual_override)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            recurring_chore_id,
            name,
            description,
            week_start,
            due_date,
            assignee_id,
            status,
            payout_cents,
            1 if manual_override else 0,
        ),
    )
    return cursor.lastrowid


def _week_contains_first_of_month(week: date) -> bool:
    due = due_date_for(week)
    first_of_due_month = due.replace(day=1)
    return week <= first_of_due_month <= due


def _should_spawn_for_week(chore: sqlite3.Row, week: date) -> bool:
    cadence = chore["cadence"]
    if cadence == "weekly":
        return True
    if cadence == "monthly":
        return _week_contains_first_of_month(week)
    return False


def spawn_week(week_start: str) -> int:
    """Spawn one raw instance per active recurring chore for ``week_start``.

    Idempotent: chores that already have an instance for the week are skipped.
    Weekly chores spawn every week, monthly chores spawn in the week containing
    the first of the month, and ad-hoc chores stay templates only.
    Returns the number of instances created. No economics happen here -- the
    instance only records that the chore exists this week; who does it and the
    transfers are derived on the client from the current preferences.
    """
    week = date.fromisoformat(week_start)
    due = due_date_for(week).isoformat()

    spawned = 0
    with connect() as conn:
        chores = conn.execute(
            "SELECT * FROM recurring_chores WHERE active = 1 ORDER BY name"
        ).fetchall()
        for chore in chores:
            if not _should_spawn_for_week(chore, week):
                continue
            exists = conn.execute(
                """
                SELECT 1 FROM chore_instances
                WHERE recurring_chore_id = ? AND week_start = ?
                """,
                (chore["id"], week_start),
            ).fetchone()
            if exists:
                continue
            _insert_instance(
                conn,
                recurring_chore_id=chore["id"],
                name=chore["name"],
                description=chore["description"],
                week_start=week_start,
                due_date=due,
                assignee_id=None,
                status="pending",
                payout_cents=0,
            )
            spawned += 1
    return spawned


def ensure_weeks_through(today: date | None = None) -> int:
    """Catch-up spawner: fill every Sunday week from the last spawned recurring
    week (or the current week if none) through the upcoming week."""
    today = today or date.today()
    target = upcoming_week(today)

    with connect() as conn:
        row = conn.execute(
            """
            SELECT MAX(week_start) AS last_week
            FROM chore_instances
            WHERE recurring_chore_id IS NOT NULL
            """
        ).fetchone()

    if row and row["last_week"]:
        week = date.fromisoformat(row["last_week"]) + timedelta(days=7)
    else:
        week = current_week(today)

    spawned = 0
    while week <= target:
        spawned += spawn_week(week.isoformat())
        week += timedelta(days=7)
    return spawned


def add_one_off_instance(
    name: str,
    description: str,
    week_start: str,
    assignee_id: int | None,
    payout_cents: int,
) -> int:
    week = date.fromisoformat(week_start)
    due = due_date_for(week).isoformat()
    with connect() as conn:
        return _insert_instance(
            conn,
            recurring_chore_id=None,
            name=name.strip(),
            description=description.strip(),
            week_start=week_start,
            due_date=due,
            assignee_id=assignee_id,
            status="pending",
            payout_cents=payout_cents,
        )


def convert_instance_to_recurring(instance_id: int, recurring_chore_id: int) -> None:
    with connect() as conn:
        instance = conn.execute(
            "SELECT recurring_chore_id FROM chore_instances WHERE id = ?",
            (instance_id,),
        ).fetchone()
        if not instance:
            raise ValueError(f"Unknown instance: {instance_id}")
        if instance["recurring_chore_id"] is not None:
            raise ValueError("Only one-off instances can be converted")

        chore = conn.execute(
            """
            SELECT * FROM recurring_chores
            WHERE id = ? AND active = 1
            """,
            (recurring_chore_id,),
        ).fetchone()
        if not chore:
            raise ValueError(f"Unknown recurring chore: {recurring_chore_id}")

        conn.execute(
            """
            UPDATE chore_instances
            SET recurring_chore_id = ?,
                name = ?,
                description = ?,
                assignee_id = NULL,
                payout_cents = 0,
                manual_override = 0
            WHERE id = ?
            """,
            (
                recurring_chore_id,
                chore["name"],
                chore["description"],
                instance_id,
            ),
        )
        conn.execute(
            "DELETE FROM chore_instance_preferences WHERE chore_instance_id = ?",
            (instance_id,),
        )


def convert_instance_to_new_recurring(
    instance_id: int,
    name: str,
    description: str,
    cadence: str,
) -> int:
    clean_name = name.strip()
    if not clean_name:
        raise ValueError("Recurring chore name is required")
    clean_description = description.strip()
    clean_cadence = _clean_cadence(cadence)
    with connect() as conn:
        existing_instance = conn.execute(
            "SELECT id FROM chore_instances WHERE id = ? AND recurring_chore_id IS NULL",
            (instance_id,),
        ).fetchone()
        if not existing_instance:
            raise ValueError("Only one-off instances can be converted")

        existing_chore = conn.execute(
            "SELECT id FROM recurring_chores WHERE name = ?",
            (clean_name,),
        ).fetchone()
        if existing_chore:
            chore_id = existing_chore["id"]
            conn.execute(
                """
                UPDATE recurring_chores
                SET description = ?, cadence = ?, active = 1
                WHERE id = ?
                """,
                (clean_description, clean_cadence, chore_id),
            )
        else:
            cursor = conn.execute(
                """
                INSERT INTO recurring_chores (name, description, cadence)
                VALUES (?, ?, ?)
                """,
                (clean_name, clean_description, clean_cadence),
            )
            chore_id = cursor.lastrowid

        conn.execute(
            """
            UPDATE chore_instances
            SET recurring_chore_id = ?,
                name = ?,
                description = ?,
                assignee_id = NULL,
                payout_cents = 0,
                manual_override = 0
            WHERE id = ?
            """,
            (chore_id, clean_name, clean_description, instance_id),
        )
        conn.execute(
            "DELETE FROM chore_instance_preferences WHERE chore_instance_id = ?",
            (instance_id,),
        )
        return chore_id


def set_manual_override(instance_id: int, assignee_id: int, payout_cents: int) -> None:
    """Turn any row (one-off or recurring) into a manual-override task: a fixed
    assignee paid a fixed price, detached from the recurring template
    ("selling the chore to a roommate")."""
    with connect() as conn:
        instance = conn.execute(
            "SELECT id FROM chore_instances WHERE id = ?",
            (instance_id,),
        ).fetchone()
        if not instance:
            raise ValueError(f"Unknown instance: {instance_id}")
        roommate = conn.execute(
            "SELECT id FROM roommates WHERE id = ? AND active = 1",
            (assignee_id,),
        ).fetchone()
        if not roommate:
            raise ValueError(f"Unknown roommate: {assignee_id}")
        conn.execute(
            """
            UPDATE chore_instances
            SET recurring_chore_id = NULL,
                assignee_id = ?,
                payout_cents = ?,
                manual_override = 1
            WHERE id = ?
            """,
            (assignee_id, payout_cents, instance_id),
        )
        conn.execute(
            "DELETE FROM chore_instance_preferences WHERE chore_instance_id = ?",
            (instance_id,),
        )


def convert_recurring_to_one_off(instance_id: int) -> None:
    """Detach a recurring instance into a plain, editable one-off (keeps its name
    and description; clears the auto assignee/price and the recurring link).

    Snapshots the recurring chore's current per-roommate wtp/bid onto the instance
    so the new one-off starts priced exactly as it was at the moment of
    conversion, instead of falling back to the one-off defaults.
    """
    with connect() as conn:
        instance = conn.execute(
            "SELECT recurring_chore_id FROM chore_instances WHERE id = ?",
            (instance_id,),
        ).fetchone()
        if not instance:
            raise ValueError(f"Unknown instance: {instance_id}")
        recurring_chore_id = instance["recurring_chore_id"]
        if recurring_chore_id is None:
            raise ValueError("Only recurring instances can be converted to one-offs")

        # Snapshot the chore's current wtp/bid for every roommate (an unset
        # preference is 0/0, exactly as the recurring computation treats it).
        saved = {
            row["roommate_id"]: (row["wtp_cents"], row["bid_cents"])
            for row in conn.execute(
                """
                SELECT roommate_id, wtp_cents, bid_cents
                FROM chore_preferences
                WHERE recurring_chore_id = ?
                """,
                (recurring_chore_id,),
            )
        }
        for roommate in conn.execute("SELECT id FROM roommates"):
            wtp, bid = saved.get(roommate["id"], (0, 0))
            conn.execute(
                """
                INSERT INTO chore_instance_preferences
                    (chore_instance_id, roommate_id, wtp_cents, bid_cents)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(chore_instance_id, roommate_id)
                DO UPDATE SET wtp_cents = excluded.wtp_cents, bid_cents = excluded.bid_cents
                """,
                (instance_id, roommate["id"], wtp, bid),
            )

        conn.execute(
            """
            UPDATE chore_instances
            SET recurring_chore_id = NULL,
                assignee_id = NULL,
                payout_cents = 0,
                manual_override = 0
            WHERE id = ?
            """,
            (instance_id,),
        )


def set_instance_status(instance_id: int, status: str) -> None:
    if status not in VALID_STATUSES:
        raise ValueError(f"Invalid status: {status!r}")
    with connect() as conn:
        conn.execute(
            "UPDATE chore_instances SET status = ? WHERE id = ?",
            (status, instance_id),
        )


_EDITABLE_COLUMNS = ("name", "description", "due_date", "status", "assignee_id")


def update_instance(instance_id: int, **changes: object) -> None:
    """Spreadsheet-style edit of a raw ledger row.

    Accepts name/description/due_date/status/assignee_id/payout_cents. ``payout``
    is stored as-is for one-offs; the client re-derives the split. No economics
    run server-side.
    """
    allowed = set(_EDITABLE_COLUMNS) | {"payout_cents"}
    unknown = set(changes) - allowed
    if unknown:
        raise ValueError(f"Unknown fields: {sorted(unknown)}")
    if "status" in changes and changes["status"] not in VALID_STATUSES:
        raise ValueError(f"Invalid status: {changes['status']!r}")

    sets: list[str] = []
    params: list[object] = []
    for column in _EDITABLE_COLUMNS:
        if column in changes:
            sets.append(f"{column} = ?")
            params.append(changes[column])
    if "payout_cents" in changes:
        sets.append("payout_cents = ?")
        params.append(changes["payout_cents"])
    if not sets:
        return

    with connect() as conn:
        conn.execute(
            f"UPDATE chore_instances SET {', '.join(sets)} WHERE id = ?",
            params + [instance_id],
        )


def delete_instance(instance_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM chore_instances WHERE id = ?", (instance_id,))


def all_instances(
    week_start: str | None = None,
    assignee_id: int | None = None,
) -> list[dict[str, object]]:
    clauses: list[str] = []
    params: list[object] = []
    if week_start:
        clauses.append("ci.week_start = ?")
        params.append(week_start)
    if assignee_id:
        clauses.append("ci.assignee_id = ?")
        params.append(assignee_id)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT ci.*
            FROM chore_instances ci
            {where}
            ORDER BY ci.week_start, ci.due_date, ci.id
            """,
            params,
        ).fetchall()

    return [
        {
            "id": row["id"],
            "recurring_chore_id": row["recurring_chore_id"],
            "name": row["name"],
            "description": row["description"],
            "week_start": row["week_start"],
            "due_date": row["due_date"],
            "assignee_id": row["assignee_id"],
            "status": row["status"],
            "payout_cents": row["payout_cents"],
            "manual_override": bool(row["manual_override"]),
            "is_one_off": row["recurring_chore_id"] is None,
        }
        for row in rows
    ]


def known_instance_weeks() -> list[str]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT week_start FROM chore_instances ORDER BY week_start DESC"
        ).fetchall()
        return [row["week_start"] for row in rows]


# --------------------------------------------------------------------------- #
# Recorded settle-up payments (roommate A pays roommate B)
# --------------------------------------------------------------------------- #
def add_roommate_payment(
    from_roommate_id: int,
    to_roommate_id: int,
    amount_cents: int,
    note: str = "",
    paid_on: str | None = None,
) -> int:
    if from_roommate_id == to_roommate_id:
        raise ValueError("A payment must be between two different roommates")
    if amount_cents <= 0:
        raise ValueError("Payment amount must be positive")
    with connect() as conn:
        for rid in (from_roommate_id, to_roommate_id):
            if not conn.execute(
                "SELECT 1 FROM roommates WHERE id = ?", (rid,)
            ).fetchone():
                raise ValueError(f"Unknown roommate: {rid}")
        cursor = conn.execute(
            """
            INSERT INTO roommate_payments
                (from_roommate_id, to_roommate_id, amount_cents, note, paid_on)
            VALUES (?, ?, ?, ?, COALESCE(?, date('now')))
            """,
            (from_roommate_id, to_roommate_id, amount_cents, note.strip(), paid_on),
        )
        return cursor.lastrowid


def list_roommate_payments() -> list[dict[str, object]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT p.*, f.name AS from_name, t.name AS to_name
            FROM roommate_payments p
            JOIN roommates f ON f.id = p.from_roommate_id
            JOIN roommates t ON t.id = p.to_roommate_id
            ORDER BY p.paid_on DESC, p.id DESC
            """
        ).fetchall()
    return [
        {
            "id": row["id"],
            "from_roommate_id": row["from_roommate_id"],
            "to_roommate_id": row["to_roommate_id"],
            "from_name": row["from_name"],
            "to_name": row["to_name"],
            "amount_cents": row["amount_cents"],
            "note": row["note"],
            "paid_on": row["paid_on"],
        }
        for row in rows
    ]


def delete_roommate_payment(payment_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM roommate_payments WHERE id = ?", (payment_id,))


# --------------------------------------------------------------------------- #
# Misc helpers
# --------------------------------------------------------------------------- #
def week_from_string(value: str) -> str:
    return date.fromisoformat(value).isoformat()


# --------------------------------------------------------------------------- #
# Mock data
# --------------------------------------------------------------------------- #
def _mock_preference(roommate_name: str, chore_name: str) -> tuple[int, int]:
    base = 800 + (sum(map(ord, chore_name)) % 9) * 100
    offset = (sum(map(ord, roommate_name)) % 5) * 75
    wtp = base + offset
    bid = wtp + 300 + (sum(map(ord, roommate_name + chore_name)) % 6) * 100
    return wtp, bid


def reset_mock_data(today: date | None = None) -> None:
    today = today or date.today()
    with connect() as conn:
        conn.executescript(
            """
            DELETE FROM chore_instance_preferences;
            DELETE FROM chore_instances;
            DELETE FROM chore_preferences;
            DELETE FROM roommates;
            DELETE FROM recurring_chores;
            DELETE FROM sqlite_sequence
            WHERE name IN (
                'chore_instance_preferences',
                'chore_instances',
                'chore_preferences',
                'roommates',
                'recurring_chores'
            );
            """
        )
        conn.executemany(
            "INSERT INTO roommates (name) VALUES (?)",
            [(name,) for name in MOCK_ROOMMATES],
        )
        # Nathan joined partway through: he shouldn't appear in weeks before this.
        conn.execute(
            "UPDATE roommates SET join_date = '2026-06-01' WHERE name = 'Nathan'"
        )
        conn.executemany(
            "INSERT INTO recurring_chores (name, description) VALUES (?, ?)",
            MOCK_RECURRING_CHORES,
        )

    roommates = active_roommates()
    chores = active_recurring_chores()
    for roommate in roommates:
        for chore in chores:
            wtp, bid = _mock_preference(roommate["name"], chore["name"])
            save_preference(roommate["id"], chore["id"], wtp, bid)

    cur = current_week(today)
    past_weeks = [cur - timedelta(days=7 * k) for k in range(3, 0, -1)]
    for week in past_weeks + [cur, upcoming_week(today)]:
        spawn_week(week.isoformat())

    # Mark the three completed weeks done, with a couple of realistic failures.
    with connect() as conn:
        for week in past_weeks:
            conn.execute(
                "UPDATE chore_instances SET status = 'done' WHERE week_start = ?",
                (week.isoformat(),),
            )
        conn.execute(
            """
            UPDATE chore_instances SET status = 'failed'
            WHERE week_start = ? AND name = 'Clean microwave'
            """,
            (past_weeks[0].isoformat(),),
        )

    first_roommate = roommates[0]["id"]
    one_off_id = add_one_off_instance(
        name="Assemble new bookshelf",
        description="One-off: build the hallway bookshelf",
        week_start=cur.isoformat(),
        assignee_id=None,
        payout_cents=0,
    )
    set_manual_override(one_off_id, first_roommate, 2500)
