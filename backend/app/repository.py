from __future__ import annotations

import sqlite3
from datetime import date

from .db import connect
from .mechanism import (
    Chore,
    ChoreLedger,
    Preference,
    Roommate,
    compute_chore_ledger,
)


ALLOWED_FREQUENCIES = {"monthly", "weekly", "one-off"}
EXAMPLE_ROOMMATES = ("Alex", "Blair", "Casey")
SHEET_ROOMMATES = ("Matthew", "Govind", "Blaine", "Emerson", "Nathan")
SHEET_LEDGER_ROWS = [
    ("2026-04-25", "Matthew", "Find cleaning person", "Once", "end of week", 2000),
    ("2026-04-25", "Matthew", "Monday Trash", "weekly", "end of week", 300),
    ("2026-04-25", "Matthew", "Thursday Trash+Recycle", "weekly", "end of week", 300),
    ("2026-04-25", "Matthew", "Putting away the dishes", "as needed (pay per week)", "end of week", 800),
    ("2026-04-25", "Matthew", "Clean the kitchen sink", "every 3 weeks", "end of week", 300),
    ("2026-04-25", "Matthew", "Clean the microwave", "every 3 weeks", "end of week", 300),
    ("2026-04-25", "Matthew", "Clean the kitchen surfaces (counters, stove, dining table)", "2/week", "end of week", 700),
    ("2026-04-27", "Govind", "Vacuum/sweep downstairs", "weekly", "end of week", 1300),
    ("2026-04-25", "Govind", "Call verizon about wifi", "once", "FAIL", 0),
    ("2026-05-02", "Matthew", "Monday Trash; Thursday Trash+Recycle", "weekly", "end of week", 1200),
    ("2026-05-02", "Blaine", "Putting away the dishes", "as needed (pay per week)", "end of week", 1600),
    ("2026-05-02", "Matthew", "Clean the kitchen surfaces (counters, stove, dining table)", "2/week", "end of week", 1000),
    ("2026-05-02", "Govind", "Vacuum/sweep downstairs and stairs", "weekly", "end of week", 1300),
    ("2026-05-11", "Matthew", "Monday Trash; Thursday Trash+Recycle", "weekly", "end of week", 1200),
    ("2026-05-11", "Matthew", "Putting away the dishes", "as needed (pay per week)", "end of week", 1300),
    ("2026-05-11", "Matthew", "Clean the kitchen surfaces (counters, stove, dining table)", "2/week", "end of week", 1000),
    ("2026-05-11", "Govind", "Vacuum/sweep downstairs and stairs", "weekly", "end of week", 1300),
    ("2026-04-25", "Emerson", "Buy and install TV on Stand", "once", "end of week", 2000),
    ("2026-05-18", "Matthew", "Monday Trash; Thursday Trash+Recycle", "weekly", "end of week", 1300),
    ("2026-05-18", "Matthew", "Putting away the dishes", "as needed (pay per week)", "end of week", 1500),
    ("2026-05-18", "Matthew", "Clean the kitchen surfaces (counters, stove, dining table)", "2/week", "end of week", 1200),
    ("2026-05-18", "Govind", "Vacuum/sweep downstairs and stairs", "weekly", "end of week", 1300),
    ("2026-05-23", "Matthew", "Monday Trash; Thursday Trash+Recycle", "weekly", "end of week", 1300),
    ("2026-05-23", "Blaine", "Putting away the dishes", "as needed (pay per week)", "end of week", 1400),
    ("2026-05-23", "Matthew", "Clean the kitchen surfaces (counters, stove, dining table)", "2/week", "end of week", 1200),
    ("2026-05-23", "Govind", "Vacuum/sweep downstairs and stairs", "weekly", "end of week", 1300),
    ("2026-05-23", "Matthew", "Clean downstairs bathroom (wipe shower, toilet, counter, clean floor, unclog drain, broadly make bathroom look good)", "every 2 weeks", "end of week", 1100),
    ("2026-05-23", "Matthew", "Clean 2nd floor bathroom (wipe shower, toilet, counter, clean floor, unclog drain, broadly make bathroom look good)", "every 2 weeks", "end of week", 1800),
    ("2026-05-23", "Matthew", "Clean Emerson bathroom (wipe shower, toilet, counter, clean floor, unclog drain, broadly make bathroom look good)", "every 2 weeks", "end of week", 2200),
    ("2026-05-23", "Govind", "Call verizon about wifi", "once", "end of week", 0),
    ("2026-05-30", "Matthew", "Monday Trash; Thursday Trash+Recycle", "weekly", "end of week", 1300),
    ("2026-05-30", "Blaine", "Putting away the dishes", "as needed (pay per week)", "end of week", 1400),
    ("2026-05-30", "Matthew", "Clean the kitchen surfaces (counters, stove, dining table)", "2/week", "end of week", 1200),
    ("2026-05-30", "Govind", "Vacuum/sweep downstairs and stairs", "weekly", "end of week", 1300),
    ("2026-05-30", "Matthew", "Coordinate pressure washing and bush trimming", "once", "something scheduled by end of week. Everything cleaned in 5 weeks", 2500),
    ("2026-05-30", "Matthew", "Clean microwave", "once", "end of week", 600),
    ("2026-05-30", "Govind", "Putting away the dishes", "as needed (pay per week)", "end of week", 1100),
    ("2026-06-06", "Matthew", "Spend 15 mins figuring out dishwasher sucking butt", "once", "end of week", 1500),
    ("2026-06-06", "Matthew", "Monday Trash; Thursday Trash+Recycle", "weekly", "end of week", 1300),
    ("2026-06-06", "Govind", "Putting away the dishes", "as needed (pay per week)", "end of week", 1300),
    ("2026-06-06", "Matthew", "Clean the kitchen surfaces (counters, stove, dining table)", "1/week", "end of week", 600),
    ("2026-06-13", "Nathan", "Monday Trash; Thursday Trash+Recycle", "weekly", "end of week", 1900),
    ("2026-06-13", "Govind", "Putting away the dishes", "as needed (pay per week)", "end of week", 1300),
    ("2026-06-13", "Nathan", "Clean the kitchen surfaces (counters, stove, dining table)", "weekly", "end of week", 700),
    ("2026-06-13", "Nathan", "Vacuum/sweep downstairs and stairs", "weekly", "end of week", 1100),
]


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


def active_chores() -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM chores WHERE active = 1 ORDER BY name"
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
    with connect() as conn:
        for name in EXAMPLE_ROOMMATES:
            existing = conn.execute(
                "SELECT id FROM roommates WHERE name = ?", (name,)
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE roommates SET active = 1 WHERE id = ?",
                    (existing["id"],),
                )
            else:
                conn.execute("INSERT INTO roommates (name) VALUES (?)", (name,))


def remove_roommate(roommate_id: int) -> None:
    with connect() as conn:
        conn.execute("UPDATE roommates SET active = 0 WHERE id = ?", (roommate_id,))


def add_chore(name: str, frequency: str, description: str) -> None:
    clean_name = name.strip()
    clean_frequency = normalize_frequency(frequency)
    clean_description = description.strip()
    if not clean_name:
        return
    with connect() as conn:
        existing = conn.execute(
            "SELECT id FROM chores WHERE name = ?", (clean_name,)
        ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE chores
                SET frequency = ?, description = ?, active = 1
                WHERE id = ?
                """,
                (clean_frequency, clean_description, existing["id"]),
            )
        else:
            conn.execute(
                """
                INSERT INTO chores (name, frequency, description)
                VALUES (?, ?, ?)
                """,
                (clean_name, clean_frequency, clean_description),
            )


def update_chore(
    chore_id: int,
    name: str,
    frequency: str,
    description: str,
) -> None:
    clean_name = name.strip()
    clean_frequency = normalize_frequency(frequency)
    clean_description = description.strip()
    if not clean_name:
        return

    with connect() as conn:
        duplicate = conn.execute(
            "SELECT id FROM chores WHERE name = ? AND id != ?",
            (clean_name, chore_id),
        ).fetchone()
        if duplicate:
            return

        conn.execute(
            """
            UPDATE chores
            SET name = ?, frequency = ?, description = ?, active = 1
            WHERE id = ?
            """,
            (clean_name, clean_frequency, clean_description, chore_id),
        )


def remove_chore(chore_id: int) -> None:
    with connect() as conn:
        conn.execute("UPDATE chores SET active = 0 WHERE id = ?", (chore_id,))


def normalize_frequency(frequency: str) -> str:
    clean = frequency.strip().lower() if frequency else "one-off"
    return clean if clean in ALLOWED_FREQUENCIES else "one-off"


def save_preferences(
    roommate_id: int,
    week_start: str,
    values: dict[int, tuple[int, int]],
) -> None:
    with connect() as conn:
        for chore_id, (wtp_cents, bid_cents) in values.items():
            conn.execute(
                """
                INSERT INTO preferences
                    (roommate_id, chore_id, week_start, wtp_cents, bid_cents)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(roommate_id, chore_id, week_start)
                DO UPDATE SET
                    wtp_cents = excluded.wtp_cents,
                    bid_cents = excluded.bid_cents,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (roommate_id, chore_id, week_start, wtp_cents, bid_cents),
            )


def effective_preferences_for_week(week_start: str) -> dict[int, dict[int, Preference]]:
    roommates = active_roommates()
    chores = active_chores()
    preference_map: dict[int, dict[int, Preference]] = {}

    with connect() as conn:
        for chore in chores:
            preference_map[chore["id"]] = {}
            for roommate in roommates:
                row = conn.execute(
                    """
                    SELECT *
                    FROM preferences
                    WHERE roommate_id = ?
                      AND chore_id = ?
                      AND week_start <= ?
                    ORDER BY week_start DESC
                    LIMIT 1
                    """,
                    (roommate["id"], chore["id"], week_start),
                ).fetchone()
                preference_map[chore["id"]][roommate["id"]] = Preference(
                    roommate_id=roommate["id"],
                    chore_id=chore["id"],
                    wtp_cents=row["wtp_cents"] if row else 0,
                    bid_cents=row["bid_cents"] if row else 0,
                    source_week=row["week_start"] if row else None,
                )

    return preference_map


def roommate_history(roommate_id: int) -> list[dict[str, object]]:
    with connect() as conn:
        weeks = [
            row["week_start"]
            for row in conn.execute(
                """
                SELECT DISTINCT week_start
                FROM preferences
                WHERE roommate_id = ?
                ORDER BY week_start ASC
                """,
                (roommate_id,),
            ).fetchall()
        ]
        rows = []
        for week in weeks:
            prefs = conn.execute(
                """
                SELECT p.*, c.name AS chore_name, c.frequency AS chore_frequency
                FROM preferences p
                JOIN chores c ON c.id = p.chore_id
                WHERE p.roommate_id = ? AND p.week_start = ?
                ORDER BY c.name
                """,
                (roommate_id, week),
            ).fetchall()
            rows.append({"week_start": week, "preferences": prefs})
        return rows


def known_weeks() -> list[str]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT week_start FROM preferences
            UNION
            SELECT week_start FROM ledger_runs
            ORDER BY week_start DESC
            """
        ).fetchall()
        return [row["week_start"] for row in rows]


def reset_mock_data() -> None:
    sheet_rows = expanded_sheet_rows()
    with connect() as conn:
        conn.executescript(
            """
            DELETE FROM ledger_payments;
            DELETE FROM ledger_entries;
            DELETE FROM ledger_runs;
            DELETE FROM preferences;
            DELETE FROM roommates;
            DELETE FROM chores;
            DELETE FROM sqlite_sequence
            WHERE name IN (
                'ledger_payments',
                'ledger_entries',
                'ledger_runs',
                'preferences',
                'roommates',
                'chores'
            );
            """
        )
        conn.executemany(
            "INSERT INTO roommates (name) VALUES (?)",
            [(name,) for name in SHEET_ROOMMATES],
        )

        chore_records = []
        for name in sorted({row[2] for row in sheet_rows}):
            matching_rows = [row for row in sheet_rows if row[2] == name]
            frequency = normalize_sheet_frequency(matching_rows[-1][3])
            description = describe_sheet_chore(matching_rows)
            chore_records.append((name, frequency, description))

        conn.executemany(
            """
            INSERT INTO chores (name, frequency, description)
            VALUES (?, ?, ?)
            """,
            chore_records,
        )

    roommates = {row["name"]: row for row in active_roommates()}
    chores = {row["name"]: row for row in active_chores()}
    weeks = sorted({row[0] for row in sheet_rows})

    for week_start in weeks:
        rows_for_week = [row for row in sheet_rows if row[0] == week_start]
        assigned_chores = {row[2]: row for row in rows_for_week}
        for roommate in roommates.values():
            values = {}
            for chore in chores.values():
                sheet_row = assigned_chores.get(chore["name"])
                if sheet_row:
                    assigned_name = sheet_row[1]
                    original_frequency = sheet_row[3]
                    listed_cost = sheet_row[5]
                    values[chore["id"]] = sheet_preference_for(
                        roommate_name=roommate["name"],
                        assigned_name=assigned_name,
                        chore_name=chore["name"],
                        original_frequency=original_frequency,
                        listed_cost=listed_cost,
                    )
                else:
                    values[chore["id"]] = inactive_sheet_preference(chore["name"])
            save_preferences(roommate["id"], week_start, values)

    for week_start in weeks:
        chore_ids = [
            chores[row[2]]["id"]
            for row in sheet_rows
            if row[0] == week_start
        ]
        record_ledger_run(week_start, chore_ids=chore_ids)


def expanded_sheet_rows() -> list[tuple[str, str, str, str, str, int]]:
    counts: dict[tuple[str, str], int] = {}
    for row in SHEET_LEDGER_ROWS:
        key = (row[0], row[2])
        counts[key] = counts.get(key, 0) + 1

    seen: dict[tuple[str, str], int] = {}
    expanded = []
    for week_start, roommate, chore_name, frequency, due_date, cost in SHEET_LEDGER_ROWS:
        key = (week_start, chore_name)
        seen[key] = seen.get(key, 0) + 1
        if counts[key] > 1:
            chore_name = f"{chore_name} ({roommate} {week_start})"
        expanded.append((week_start, roommate, chore_name, frequency, due_date, cost))
    return expanded


def normalize_sheet_frequency(frequency: str) -> str:
    clean = frequency.strip().lower()
    if "once" in clean:
        return "one-off"
    if "3 weeks" in clean or "2 weeks" in clean:
        return "monthly"
    return "weekly"


def describe_sheet_chore(rows: list[tuple[str, str, str, str, str, int]]) -> str:
    latest = rows[-1]
    costs = sorted({row[5] for row in rows})
    cost_text = ", ".join(cents_to_text(cost) for cost in costs)
    return (
        f"Sheet frequency: {latest[3]}; due: {latest[4]}; "
        f"listed cost(s): {cost_text}."
    )


def sheet_preference_for(
    roommate_name: str,
    assigned_name: str,
    chore_name: str,
    original_frequency: str,
    listed_cost: int,
) -> tuple[int, int]:
    wtp = intuitive_wtp(chore_name, original_frequency, listed_cost)
    if listed_cost <= 0:
        return (wtp, 0 if roommate_name == assigned_name else max(100, wtp + 200))

    if roommate_name == assigned_name:
        bid = max(100, listed_cost)
    else:
        bid = max(listed_cost + 500, wtp + 300)
    return (wtp, bid)


def inactive_sheet_preference(chore_name: str) -> tuple[int, int]:
    wtp = max(100, intuitive_wtp(chore_name, "", 0) // 4)
    return (wtp, wtp + 500)


def intuitive_wtp(chore_name: str, frequency: str, listed_cost: int) -> int:
    text = f"{chore_name} {frequency}".lower()
    base = 500
    if "trash" in text or "recycle" in text:
        base = 2200
    elif "bathroom" in text:
        base = 2000
    elif "dishes" in text:
        base = 1700
    elif "vacuum" in text or "sweep" in text:
        base = 1500
    elif "kitchen surfaces" in text or "sink" in text or "microwave" in text:
        base = 1100
    elif "wifi" in text or "verizon" in text or "dishwasher" in text:
        base = 1200
    elif "pressure" in text or "cleaning person" in text:
        base = 900
    elif "tv" in text:
        base = 700

    if "2/week" in text:
        base += 350
    if "weekly" in text:
        base += 250
    if "once" in text:
        base -= 100

    return max(100, base + (listed_cost // 3))


def cents_to_text(value: int) -> str:
    return f"${value // 100}.{value % 100:02d}"


def compute_week_ledger(
    week_start: str,
    chore_ids: list[int] | None = None,
) -> list[ChoreLedger]:
    roommates = [
        Roommate(id=row["id"], name=row["name"])
        for row in active_roommates()
    ]
    chores = [
        Chore(id=row["id"], name=row["name"], frequency=row["frequency"])
        for row in active_chores()
        if chore_ids is None or row["id"] in chore_ids
    ]
    preference_map = effective_preferences_for_week(week_start)
    return [
        compute_chore_ledger(chore, roommates, preference_map[chore.id])
        for chore in chores
    ]


def record_ledger_run(week_start: str, chore_ids: list[int] | None = None) -> None:
    entries = compute_week_ledger(week_start, chore_ids=chore_ids)
    with connect() as conn:
        conn.execute("DELETE FROM ledger_runs WHERE week_start = ?", (week_start,))
        cursor = conn.execute(
            "INSERT INTO ledger_runs (week_start) VALUES (?)", (week_start,)
        )
        run_id = cursor.lastrowid
        for entry in entries:
            cursor = conn.execute(
                """
                INSERT INTO ledger_entries
                    (run_id, week_start, chore_id, assignee_id, surplus_cents, notes)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    week_start,
                    entry.chore.id,
                    entry.assignee.id if entry.assignee else None,
                    entry.surplus_cents,
                    entry.notes,
                ),
            )
            entry_id = cursor.lastrowid
            for roommate_id, amount_cents in entry.payments.items():
                conn.execute(
                    """
                    INSERT INTO ledger_payments
                        (entry_id, roommate_id, amount_cents)
                    VALUES (?, ?, ?)
                    """,
                    (entry_id, roommate_id, amount_cents),
                )


def saved_ledger_weeks() -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM ledger_runs ORDER BY week_start DESC"
        ).fetchall()


def saved_ledger_weeks_ascending() -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM ledger_runs ORDER BY week_start ASC"
        ).fetchall()


def saved_ledger_for_week(week_start: str) -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(
            """
            SELECT
                e.id AS entry_id,
                e.week_start,
                e.surplus_cents,
                c.name AS chore_name,
                c.frequency,
                c.description AS chore_description,
                r.name AS assignee_name,
                p.amount_cents,
                payer.name AS roommate_name
            FROM ledger_entries e
            JOIN chores c ON c.id = e.chore_id
            LEFT JOIN roommates r ON r.id = e.assignee_id
            JOIN ledger_payments p ON p.entry_id = e.id
            JOIN roommates payer ON payer.id = p.roommate_id
            WHERE e.week_start = ?
            ORDER BY c.name, payer.name
            """,
            (week_start,),
        ).fetchall()


def overall_balances() -> dict[str, object]:
    with connect() as conn:
        net_rows = conn.execute(
            """
            SELECT r.id, r.name, COALESCE(SUM(p.amount_cents), 0) AS net_cents
            FROM roommates r
            LEFT JOIN ledger_payments p ON p.roommate_id = r.id
            GROUP BY r.id, r.name
            ORDER BY r.name
            """
        ).fetchall()

    settlements = settle_balances(
        {row["id"]: {"name": row["name"], "net_cents": row["net_cents"]} for row in net_rows}
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


def week_from_string(value: str) -> str:
    parsed = date.fromisoformat(value)
    return parsed.isoformat()
