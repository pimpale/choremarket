from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta

from .db import connect
from .mock_data import MOCK_ROOMMATES
from .weeks import current_week, due_date_for, upcoming_week


VALID_STATUSES = {"pending", "done", "failed"}
# Exact-budget-balance mechanisms only; the client derives all transfers.
MECHANISMS = {"first-best", "vickrey-majority", "vickrey-faltings"}
DEFAULT_MECHANISM = "first-best"
CADENCES = {"weekly", "monthly", "ad-hoc"}

# (name, description, cadence). The real chore list as our house reorganized it
# in the 2026-07-26 ledger export, its latest fully-priced week.
MOCK_RECURRING_CHORES = [
    ("Clean downstairs bathroom", "", "weekly"),
    ("Clean microwave + kitchen sink", "Wipe down the microwave inside and out", "weekly"),
    ("Clean table and kitchen surfaces", "Clear surfaces and wipe down", "weekly"),
    ("Dishes", "put away dishes. Load dishwasher if dishes are accumulated on counter.", "weekly"),
    ("Monday + Th Trash + Recycle", "Take out Monday trash, Take out Th trash + Recycle", "weekly"),
    ("Vacuum/sweep downstairs", "Vacuum and sweep downstairs and the stairs", "weekly"),
    ("clean middle floor bathroom", "", "weekly"),
    ("clean top floor bathroom", "", "weekly"),
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
    # Falls back to the default for retired values (agv/vcg/bailey-cavallo)
    # still stored in older databases.
    value = get_setting("mechanism", DEFAULT_MECHANISM)
    return value if value in MECHANISMS else DEFAULT_MECHANISM


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
# Preferences (append-only wtp/bid edit log keyed by recurring chore)
#
# Each (roommate, recurring chore) has a *history* of wtp/bid edits, each stamped
# with when it takes effect (created_at). The value in force for a given week is
# the most recent edit that landed on or before the end of that week. Editing a
# bid just appends a new row: an edit stamped now reprices the current week and
# forward, an edit stamped at a future week's start reprices from that week
# only, and settled past weeks keep whatever value was in force then. The whole
# edit history is preserved.
# --------------------------------------------------------------------------- #

# Sentinel "since the beginning of time": preferences saved with this timestamp
# (mock data) apply to every week, past included.
BASE_CREATED_AT = "1970-01-01 00:00:00"


def save_preference(
    roommate_id: int,
    recurring_chore_id: int,
    wtp_cents: int | None,
    bid_cents: int | None,
    created_at: str | None = None,
) -> None:
    """Append a wtp/bid edit for a (roommate, chore).

    ``created_at`` defaults to now, so the edit takes effect for the current week
    onward and leaves settled past weeks untouched. Callers editing a future
    week's row pass that week's start so the edit only applies from that week;
    mock/seed data passes the beginning-of-time sentinel so the value applies to
    every week.
    """
    with connect() as conn:
        if created_at is None:
            conn.execute(
                """
                INSERT INTO chore_preferences
                    (roommate_id, recurring_chore_id, wtp_cents, bid_cents)
                VALUES (?, ?, ?, ?)
                """,
                (roommate_id, recurring_chore_id, wtp_cents, bid_cents),
            )
        else:
            conn.execute(
                """
                INSERT INTO chore_preferences
                    (roommate_id, recurring_chore_id, wtp_cents, bid_cents, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (roommate_id, recurring_chore_id, wtp_cents, bid_cents, created_at),
            )


def preferences_by_chore(
    as_of_week: str | None = None,
) -> dict[int, dict[int, dict[str, int | None]]]:
    """{recurring_chore_id: {roommate_id: {wtp_cents, bid_cents}}} in force as of
    ``as_of_week`` (defaults to the current week).

    Covers every roommate (not just current members) so the client can recompute
    historical weeks for people who have since left; the client filters who
    actually participates in each week by membership dates. Unset pairs come back
    as NULL/NULL (treated as $0 WTP and a very large bid, like one-offs).
    """
    as_of = as_of_week or current_week().isoformat()
    week_end = due_date_for(date.fromisoformat(as_of)).isoformat()
    history = preference_history_by_chore()
    return {
        chore_id: {
            roommate_id: _effective_pref_from_history(entries, week_end)
            for roommate_id, entries in by_roommate.items()
        }
        for chore_id, by_roommate in history.items()
    }


def _effective_pref_from_history(
    entries: list[dict[str, object]], week_end: str
) -> dict[str, int | None]:
    """Pick the value in force for a week (whose last day is ``week_end``) from a
    (roommate, chore) edit log sorted by created_at ascending: the most recent
    edit made on or before that day. Unset -> NULL/NULL."""
    chosen = None
    for entry in entries:
        if str(entry["created_at"])[:10] <= week_end:
            chosen = entry
        else:
            break
    if chosen is None:
        return {"wtp_cents": None, "bid_cents": None}
    return {"wtp_cents": chosen["wtp_cents"], "bid_cents": chosen["bid_cents"]}


def preference_history_by_chore() -> dict[int, dict[int, list[dict[str, object]]]]:
    """{recurring_chore_id: {roommate_id: [{created_at, wtp_cents, bid_cents}]}}.

    Each roommate's list is the full edit log, sorted by created_at ascending.
    The client resolves the value in force for each ledger week, so it can render
    every week's economics from the value that was effective then.
    """
    roommates = all_roommates()
    chores = active_recurring_chores()
    out: dict[int, dict[int, list[dict[str, object]]]] = {}
    with connect() as conn:
        for chore in chores:
            out[chore["id"]] = {}
            for roommate in roommates:
                rows = conn.execute(
                    """
                    SELECT created_at, wtp_cents, bid_cents
                    FROM chore_preferences
                    WHERE roommate_id = ? AND recurring_chore_id = ?
                    ORDER BY created_at, id
                    """,
                    (roommate["id"], chore["id"]),
                ).fetchall()
                out[chore["id"]][roommate["id"]] = [
                    {
                        "created_at": row["created_at"],
                        "wtp_cents": row["wtp_cents"],
                        "bid_cents": row["bid_cents"],
                    }
                    for row in rows
                ]
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

    Snapshots the recurring chore's per-roommate wtp/bid *as it was effective for
    this instance's week* onto the instance, so the new one-off starts priced
    exactly as it was at the moment of conversion, instead of falling back to the
    one-off defaults. An unset preference is snapshotted as NULL/NULL, which the
    one-off computation treats identically to the recurring one (no WTP, a very
    large bid).
    """
    with connect() as conn:
        instance = conn.execute(
            "SELECT recurring_chore_id, week_start FROM chore_instances WHERE id = ?",
            (instance_id,),
        ).fetchone()
        if not instance:
            raise ValueError(f"Unknown instance: {instance_id}")
        recurring_chore_id = instance["recurring_chore_id"]
        if recurring_chore_id is None:
            raise ValueError("Only recurring instances can be converted to one-offs")

        # Snapshot the chore's wtp/bid effective for this instance's week for
        # every roommate (an unset preference stays NULL/NULL).
        week_end = due_date_for(date.fromisoformat(instance["week_start"])).isoformat()
        roommate_ids = [row["id"] for row in conn.execute("SELECT id FROM roommates")]
        for roommate_id in roommate_ids:
            rows = conn.execute(
                """
                SELECT created_at, wtp_cents, bid_cents
                FROM chore_preferences
                WHERE roommate_id = ? AND recurring_chore_id = ?
                ORDER BY created_at, id
                """,
                (roommate_id, recurring_chore_id),
            ).fetchall()
            pref = _effective_pref_from_history([dict(row) for row in rows], week_end)
            wtp, bid = pref["wtp_cents"], pref["bid_cents"]
            conn.execute(
                """
                INSERT INTO chore_instance_preferences
                    (chore_instance_id, roommate_id, wtp_cents, bid_cents)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(chore_instance_id, roommate_id)
                DO UPDATE SET wtp_cents = excluded.wtp_cents, bid_cents = excluded.bid_cents
                """,
                (instance_id, roommate_id, wtp, bid),
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
#
# The mock world is a snapshot of our house's real ledger: the chore list and
# every roommate's actual wtp/bid exactly as they stood in the 2026-07-26
# week, when the chores were reorganized into this set. History weeks are
# spawned priced by these same prefs -- including 2026-07-19, whose real
# bids were lost to a code outage that week, so it's backfilled with the
# 07-26 numbers too -- whether each past chore got done is seeded-random.
# --------------------------------------------------------------------------- #

# {chore_name: {roommate_name: (wtp_cents, bid_cents)}}, hand-copied from the
# 2026-07-26 week of the real ledger export.
MOCK_PREFS: dict[str, dict[str, tuple[int, int]]] = {
    "Clean downstairs bathroom": {
        "Blaine": (500, 30000),
        "Emerson": (1000, 2900),
        "Govind": (1000, 2000),
        "Matthew": (1000, 1400),
        "Nathan": (3000, 1500),
    },
    "Clean microwave + kitchen sink": {
        "Blaine": (100, 3000),
        "Emerson": (400, 1900),
        "Govind": (1100, 800),
        "Matthew": (400, 1200),
        "Nathan": (1500, 1000),
    },
    "Clean table and kitchen surfaces": {
        "Blaine": (500, 5000),
        "Emerson": (300, 2900),
        "Govind": (500, 1200),
        "Matthew": (1000, 2000),
        "Nathan": (1500, 1000),
    },
    "Dishes": {
        "Blaine": (800, 5000),
        "Emerson": (500, 4900),
        "Govind": (5000, 2000),
        "Matthew": (2000, 4000),
        "Nathan": (3500, 2500),
    },
    "Monday + Th Trash + Recycle": {
        "Blaine": (1000, 5000),
        "Emerson": (3000, 1900),
        "Govind": (5000, 2000),
        "Matthew": (400, 2800),
        "Nathan": (5000, 2000),
    },
    "Vacuum/sweep downstairs": {
        "Blaine": (400, 5000),
        "Emerson": (200, 2400),
        "Govind": (1300, 2100),
        "Matthew": (800, 3500),
        "Nathan": (3000, 2500),
    },
    "clean middle floor bathroom": {
        "Blaine": (500, 99900),
        "Emerson": (0, 99900),
        "Govind": (1000, 3000),
        "Matthew": (2000, 2000),
        "Nathan": (1000, 2000),
    },
    "clean top floor bathroom": {
        "Blaine": (500, 99900),
        "Emerson": (1500, 1855),
        "Govind": (0, 3000),
        "Matthew": (3000, 3000),
        "Nathan": (2500, 2000),
    },
}


@dataclass
class MockAssumptions:
    """Knobs for regenerating the mock world."""

    # First week the app was actually used irl; there's no real history
    # before this, so mock history starts here instead of some fixed count
    # of weeks back.
    history_start: str = "2026-07-19"
    nathan_join: str = "2026-06-01"


# One-off tasks lifted straight from the real ledger, tied to the actual week
# they appeared in: (name, description, week_start, prefs). Both were marked
# "skipped" in the ledger (proposed, never completed) -- the closest fit in
# our status model is "failed". Rows with no chore name (blank spreadsheet
# rows) and one-offs from weeks before history_start aren't real data and are
# left out.
_MOCK_ONE_OFFS = [
    (
        "fix table",
        "",
        "2026-07-19",
        {"Govind": (500, 2500), "Nathan": (200, 1300)},
    ),
    (
        "Fix chinesium table",
        "",
        "2026-07-19",
        {},
    ),
]


def reset_mock_data(
    today: date | None = None,
    assumptions: MockAssumptions | None = None,
) -> None:
    today = today or date.today()
    assumptions = assumptions or MockAssumptions()
    prefs = MOCK_PREFS

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
            "UPDATE roommates SET join_date = ? WHERE name = 'Nathan'",
            (assumptions.nathan_join,),
        )
        conn.executemany(
            "INSERT INTO recurring_chores (name, description, cadence) VALUES (?, ?, ?)",
            MOCK_RECURRING_CHORES,
        )

    roommate_id = {r["name"]: r["id"] for r in active_roommates()}
    chore_id = {c["name"]: c["id"] for c in active_recurring_chores()}
    for chore_name, by_person in prefs.items():
        for person, (wtp, bid) in by_person.items():
            # Seed at the beginning of time so the mock prefs price every week,
            # including the generated history.
            save_preference(
                roommate_id[person],
                chore_id[chore_name],
                wtp,
                bid,
                created_at=BASE_CREATED_AT,
            )

    cur = current_week(today)
    history_start = date.fromisoformat(assumptions.history_start)
    past_weeks = []
    week = history_start
    while week < cur:
        past_weeks.append(week)
        week += timedelta(days=7)
    for week in past_weeks + [cur, upcoming_week(today)]:
        spawn_week(week.isoformat())

    # Past weeks are settled history: every recurring chore in them was
    # completed. The current and upcoming weeks stay pending.
    if past_weeks:
        with connect() as conn:
            conn.execute(
                """
                UPDATE chore_instances SET status = 'done'
                WHERE recurring_chore_id IS NOT NULL AND week_start < ?
                """,
                (cur.isoformat(),),
            )

    # One-off tasks from the real ledger, seeded into their actual week.
    for name, description, week_start, instance_prefs in _MOCK_ONE_OFFS:
        instance_id = add_one_off_instance(
            name=name,
            description=description,
            week_start=week_start,
            assignee_id=None,
            payout_cents=0,
        )
        for person, (wtp, bid) in instance_prefs.items():
            save_instance_preference(instance_id, roommate_id[person], wtp, bid)
        set_instance_status(instance_id, "failed")
