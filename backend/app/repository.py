from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from .db import connect
from .mechanism import (
    Chore,
    ChoreLedger,
    Preference,
    Roommate,
    compute_chore_ledger,
    current_week,
    due_date_for,
    flat_payout_payments,
    upcoming_week,
)


VALID_STATUSES = {"pending", "done", "failed"}
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


def add_roommate(name: str) -> None:
    clean = name.strip()
    if not clean:
        return
    with connect() as conn:
        existing = conn.execute(
            "SELECT id FROM roommates WHERE name = ?", (clean,)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE roommates SET active = 1 WHERE id = ?", (existing["id"],)
            )
        else:
            conn.execute("INSERT INTO roommates (name) VALUES (?)", (clean,))


def add_example_roommates() -> None:
    for name in EXAMPLE_ROOMMATES:
        add_roommate(name)


def remove_roommate(roommate_id: int) -> None:
    with connect() as conn:
        conn.execute("UPDATE roommates SET active = 0 WHERE id = ?", (roommate_id,))


# --------------------------------------------------------------------------- #
# Recurring chores (templates)
# --------------------------------------------------------------------------- #
def active_recurring_chores() -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM recurring_chores WHERE active = 1 ORDER BY name"
        ).fetchall()


def add_recurring_chore(name: str, description: str) -> None:
    clean_name = name.strip()
    clean_description = description.strip()
    if not clean_name:
        return
    with connect() as conn:
        existing = conn.execute(
            "SELECT id FROM recurring_chores WHERE name = ?", (clean_name,)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE recurring_chores SET description = ?, active = 1 WHERE id = ?",
                (clean_description, existing["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO recurring_chores (name, description) VALUES (?, ?)",
                (clean_name, clean_description),
            )


def update_recurring_chore(chore_id: int, name: str, description: str) -> None:
    clean_name = name.strip()
    clean_description = description.strip()
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
            "UPDATE recurring_chores SET name = ?, description = ?, active = 1 WHERE id = ?",
            (clean_name, clean_description, chore_id),
        )


def remove_recurring_chore(chore_id: int) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE recurring_chores SET active = 0 WHERE id = ?", (chore_id,)
        )


# --------------------------------------------------------------------------- #
# Preferences (keyed by recurring chore, week-independent)
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


def preferences_grid() -> dict[int, dict[int, Preference]]:
    """preferences[roommate_id][recurring_chore_id] for active roommates/chores."""
    roommates = active_roommates()
    chores = active_recurring_chores()
    grid: dict[int, dict[int, Preference]] = {}
    with connect() as conn:
        for roommate in roommates:
            grid[roommate["id"]] = {}
            for chore in chores:
                row = conn.execute(
                    """
                    SELECT wtp_cents, bid_cents
                    FROM chore_preferences
                    WHERE roommate_id = ? AND recurring_chore_id = ?
                    """,
                    (roommate["id"], chore["id"]),
                ).fetchone()
                grid[roommate["id"]][chore["id"]] = Preference(
                    roommate_id=roommate["id"],
                    chore_id=chore["id"],
                    wtp_cents=row["wtp_cents"] if row else 0,
                    bid_cents=row["bid_cents"] if row else 0,
                )
    return grid


def _preference_map(
    conn: sqlite3.Connection,
    recurring_chore_id: int,
    roommates: list[Roommate],
) -> dict[int, Preference]:
    out: dict[int, Preference] = {}
    for roommate in roommates:
        row = conn.execute(
            """
            SELECT wtp_cents, bid_cents
            FROM chore_preferences
            WHERE roommate_id = ? AND recurring_chore_id = ?
            """,
            (roommate.id, recurring_chore_id),
        ).fetchone()
        out[roommate.id] = Preference(
            roommate_id=roommate.id,
            chore_id=recurring_chore_id,
            wtp_cents=row["wtp_cents"] if row else 0,
            bid_cents=row["bid_cents"] if row else 0,
        )
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
    surplus_cents: int,
    notes: str,
    payments: dict[int, int],
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO chore_instances
            (recurring_chore_id, name, description, week_start, due_date,
             assignee_id, status, surplus_cents, notes)
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
            surplus_cents,
            notes,
        ),
    )
    instance_id = cursor.lastrowid
    for roommate_id, amount_cents in payments.items():
        conn.execute(
            """
            INSERT INTO instance_payments (instance_id, roommate_id, amount_cents)
            VALUES (?, ?, ?)
            """,
            (instance_id, roommate_id, amount_cents),
        )
    return instance_id


def spawn_week(week_start: str) -> int:
    """Spawn one instance per active recurring chore for ``week_start``.

    Idempotent: chores that already have an instance for the week are skipped
    (enforced both here and by the UNIQUE(recurring_chore_id, week_start) guard).
    Returns the number of instances created.
    """
    week = date.fromisoformat(week_start)
    due = due_date_for(week).isoformat()
    roommates = [Roommate(id=row["id"], name=row["name"]) for row in active_roommates()]

    spawned = 0
    with connect() as conn:
        chores = conn.execute(
            "SELECT * FROM recurring_chores WHERE active = 1 ORDER BY name"
        ).fetchall()
        for chore in chores:
            exists = conn.execute(
                """
                SELECT 1 FROM chore_instances
                WHERE recurring_chore_id = ? AND week_start = ?
                """,
                (chore["id"], week_start),
            ).fetchone()
            if exists:
                continue
            prefs = _preference_map(conn, chore["id"], roommates)
            ledger = compute_chore_ledger(
                Chore(id=chore["id"], name=chore["name"]), roommates, prefs
            )
            _insert_instance(
                conn,
                recurring_chore_id=chore["id"],
                name=chore["name"],
                description=chore["description"],
                week_start=week_start,
                due_date=due,
                assignee_id=ledger.assignee.id if ledger.assignee else None,
                status="pending",
                surplus_cents=ledger.surplus_cents,
                notes=ledger.notes,
                payments=ledger.payments,
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
    roommate_ids = [row["id"] for row in active_roommates()]
    payments = (
        flat_payout_payments(assignee_id, roommate_ids, payout_cents)
        if assignee_id is not None
        else {rid: 0 for rid in roommate_ids}
    )
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
            surplus_cents=payout_cents,
            notes="One-off entry.",
            payments=payments,
        )


def set_instance_status(instance_id: int, status: str) -> None:
    if status not in VALID_STATUSES:
        raise ValueError(f"Invalid status: {status!r}")
    with connect() as conn:
        conn.execute(
            "UPDATE chore_instances SET status = ? WHERE id = ?",
            (status, instance_id),
        )


_EDITABLE_COLUMNS = ("name", "description", "due_date", "status")


def update_instance(instance_id: int, **changes: object) -> None:
    """Spreadsheet-style edit of a ledger row.

    Accepts any of name/description/due_date/status/assignee_id/payout_cents.
    Changing the assignee or payout re-derives the row's payment snapshot:
    one-offs use a flat payout split, recurring rows re-run the AGV transfer
    with the chosen assignee.
    """
    allowed = set(_EDITABLE_COLUMNS) | {"assignee_id", "payout_cents"}
    unknown = set(changes) - allowed
    if unknown:
        raise ValueError(f"Unknown fields: {sorted(unknown)}")
    if "status" in changes and changes["status"] not in VALID_STATUSES:
        raise ValueError(f"Invalid status: {changes['status']!r}")

    with connect() as conn:
        row = conn.execute(
            "SELECT id FROM chore_instances WHERE id = ?", (instance_id,)
        ).fetchone()
        if row is None:
            return

        sets: list[str] = []
        params: list[object] = []
        for column in _EDITABLE_COLUMNS:
            if column in changes:
                sets.append(f"{column} = ?")
                params.append(changes[column])
        if "assignee_id" in changes:
            sets.append("assignee_id = ?")
            params.append(changes["assignee_id"])
        if sets:
            conn.execute(
                f"UPDATE chore_instances SET {', '.join(sets)} WHERE id = ?",
                params + [instance_id],
            )

        if "assignee_id" in changes or "payout_cents" in changes:
            _recompute_instance_payments(
                conn,
                instance_id,
                payout_override=changes.get("payout_cents"),
            )


def _recompute_instance_payments(
    conn: sqlite3.Connection,
    instance_id: int,
    payout_override: object | None = None,
) -> None:
    row = conn.execute(
        "SELECT * FROM chore_instances WHERE id = ?", (instance_id,)
    ).fetchone()
    roommates = [
        Roommate(id=r["id"], name=r["name"])
        for r in conn.execute(
            "SELECT * FROM roommates WHERE active = 1 ORDER BY name"
        ).fetchall()
    ]
    roommate_ids = [r.id for r in roommates]

    if row["recurring_chore_id"] is None:
        payout = (
            int(payout_override)
            if payout_override is not None
            else row["surplus_cents"]
        )
        assignee_id = row["assignee_id"]
        payments = (
            flat_payout_payments(assignee_id, roommate_ids, payout)
            if assignee_id is not None
            else {rid: 0 for rid in roommate_ids}
        )
        surplus = payout
    else:
        chore = conn.execute(
            "SELECT name FROM recurring_chores WHERE id = ?",
            (row["recurring_chore_id"],),
        ).fetchone()
        prefs = _preference_map(conn, row["recurring_chore_id"], roommates)
        ledger = compute_chore_ledger(
            Chore(id=row["recurring_chore_id"], name=chore["name"]),
            roommates,
            prefs,
            forced_assignee_id=row["assignee_id"],
        )
        payments = ledger.payments
        surplus = ledger.surplus_cents
        assignee_id = ledger.assignee.id if ledger.assignee else None

    conn.execute("DELETE FROM instance_payments WHERE instance_id = ?", (instance_id,))
    conn.execute(
        "UPDATE chore_instances SET assignee_id = ?, surplus_cents = ? WHERE id = ?",
        (assignee_id, surplus, instance_id),
    )
    for roommate_id, amount_cents in payments.items():
        conn.execute(
            """
            INSERT INTO instance_payments (instance_id, roommate_id, amount_cents)
            VALUES (?, ?, ?)
            """,
            (instance_id, roommate_id, amount_cents),
        )


def delete_instance(instance_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM chore_instances WHERE id = ?", (instance_id,))


def recompute_week(week_start: str) -> int:
    """Refresh AGV snapshots for not-yet-done recurring instances of a week.

    Used after preferences change. One-off rows keep their manual snapshot.
    """
    roommates = [Roommate(id=row["id"], name=row["name"]) for row in active_roommates()]
    updated = 0
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT ci.*, rc.name AS chore_name
            FROM chore_instances ci
            JOIN recurring_chores rc ON rc.id = ci.recurring_chore_id
            WHERE ci.week_start = ? AND ci.status != 'done'
            """,
            (week_start,),
        ).fetchall()
        for row in rows:
            prefs = _preference_map(conn, row["recurring_chore_id"], roommates)
            ledger = compute_chore_ledger(
                Chore(id=row["recurring_chore_id"], name=row["chore_name"]),
                roommates,
                prefs,
            )
            conn.execute(
                "DELETE FROM instance_payments WHERE instance_id = ?", (row["id"],)
            )
            conn.execute(
                """
                UPDATE chore_instances
                SET assignee_id = ?, surplus_cents = ?, notes = ?
                WHERE id = ?
                """,
                (
                    ledger.assignee.id if ledger.assignee else None,
                    ledger.surplus_cents,
                    ledger.notes,
                    row["id"],
                ),
            )
            for roommate_id, amount_cents in ledger.payments.items():
                conn.execute(
                    """
                    INSERT INTO instance_payments
                        (instance_id, roommate_id, amount_cents)
                    VALUES (?, ?, ?)
                    """,
                    (row["id"], roommate_id, amount_cents),
                )
            updated += 1
    return updated


def recompute_future_weeks(today: date | None = None) -> int:
    """Refresh non-done recurring instances for the current week onward.

    Called automatically after preferences change so the ledger stays in sync
    without a manual button.
    """
    today = today or date.today()
    cutoff = current_week(today).isoformat()
    updated = 0
    for week in known_instance_weeks():
        if week >= cutoff:
            updated += recompute_week(week)
    return updated


def preferences_by_chore() -> dict[int, dict[int, dict[str, int]]]:
    """{recurring_chore_id: {roommate_id: {wtp_cents, bid_cents}}} for the grid."""
    grid = preferences_grid()
    out: dict[int, dict[int, dict[str, int]]] = {}
    for roommate_id, chores in grid.items():
        for chore_id, pref in chores.items():
            out.setdefault(chore_id, {})[roommate_id] = {
                "wtp_cents": pref.wtp_cents,
                "bid_cents": pref.bid_cents,
            }
    return out


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
            SELECT ci.*, r.name AS assignee_name
            FROM chore_instances ci
            LEFT JOIN roommates r ON r.id = ci.assignee_id
            {where}
            ORDER BY ci.week_start, ci.due_date, ci.name, ci.id
            """,
            params,
        ).fetchall()

        result = []
        for row in rows:
            payments = conn.execute(
                """
                SELECT p.roommate_id, p.amount_cents, rm.name AS roommate_name
                FROM instance_payments p
                JOIN roommates rm ON rm.id = p.roommate_id
                WHERE p.instance_id = ?
                ORDER BY rm.name
                """,
                (row["id"],),
            ).fetchall()
            result.append(
                {
                    "id": row["id"],
                    "recurring_chore_id": row["recurring_chore_id"],
                    "name": row["name"],
                    "description": row["description"],
                    "week_start": row["week_start"],
                    "due_date": row["due_date"],
                    "assignee_id": row["assignee_id"],
                    "assignee_name": row["assignee_name"],
                    "status": row["status"],
                    "surplus_cents": row["surplus_cents"],
                    "notes": row["notes"],
                    "is_one_off": row["recurring_chore_id"] is None,
                    "payments": [
                        {
                            "roommate_id": payment["roommate_id"],
                            "roommate_name": payment["roommate_name"],
                            "amount_cents": payment["amount_cents"],
                        }
                        for payment in payments
                    ],
                }
            )
        return result


def known_instance_weeks() -> list[str]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT week_start FROM chore_instances ORDER BY week_start DESC"
        ).fetchall()
        return [row["week_start"] for row in rows]


# --------------------------------------------------------------------------- #
# Balances (only 'done' instances pay out)
# --------------------------------------------------------------------------- #
def overall_balances() -> dict[str, object]:
    with connect() as conn:
        net_rows = conn.execute(
            """
            SELECT
                r.id,
                r.name,
                COALESCE(
                    SUM(CASE WHEN ci.status = 'done' THEN p.amount_cents ELSE 0 END),
                    0
                ) AS net_cents
            FROM roommates r
            LEFT JOIN instance_payments p ON p.roommate_id = r.id
            LEFT JOIN chore_instances ci ON ci.id = p.instance_id
            WHERE r.active = 1
            GROUP BY r.id, r.name
            ORDER BY r.name
            """
        ).fetchall()

    settlements = settle_balances(
        {
            row["id"]: {"name": row["name"], "net_cents": row["net_cents"]}
            for row in net_rows
        }
    )
    return {"nets": net_rows, "settlements": settlements}


def settle_balances(
    nets: dict[int, dict[str, int | str]]
) -> list[dict[str, int | str]]:
    debtors = [
        {"id": pid, "name": str(data["name"]), "amount": int(data["net_cents"])}
        for pid, data in nets.items()
        if int(data["net_cents"]) > 0
    ]
    creditors = [
        {"id": pid, "name": str(data["name"]), "amount": -int(data["net_cents"])}
        for pid, data in nets.items()
        if int(data["net_cents"]) < 0
    ]

    settlements: list[dict[str, int | str]] = []
    i = j = 0
    while i < len(debtors) and j < len(creditors):
        amount = min(debtors[i]["amount"], creditors[j]["amount"])
        if amount:
            settlements.append(
                {
                    "from": debtors[i]["name"],
                    "to": creditors[j]["name"],
                    "amount_cents": amount,
                }
            )
        debtors[i]["amount"] -= amount
        creditors[j]["amount"] -= amount
        if debtors[i]["amount"] == 0:
            i += 1
        if creditors[j]["amount"] == 0:
            j += 1

    return settlements


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
            DELETE FROM instance_payments;
            DELETE FROM chore_instances;
            DELETE FROM chore_preferences;
            DELETE FROM roommates;
            DELETE FROM recurring_chores;
            DELETE FROM sqlite_sequence
            WHERE name IN (
                'instance_payments',
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
        # One chore slips in the oldest week.
        conn.execute(
            """
            UPDATE chore_instances SET status = 'failed'
            WHERE week_start = ? AND name = 'Clean microwave'
            """,
            (past_weeks[0].isoformat(),),
        )

    # A directly-entered one-off in the current week.
    first_roommate = roommates[0]["id"]
    add_one_off_instance(
        name="Assemble new bookshelf",
        description="One-off: build the hallway bookshelf",
        week_start=cur.isoformat(),
        assignee_id=first_roommate,
        payout_cents=2500,
    )
