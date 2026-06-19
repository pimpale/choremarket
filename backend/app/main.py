from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date, timedelta

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import repository
from .db import init_db
from .mechanism import upcoming_week


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Choremarket", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:5174",
        "http://localhost:5174",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class RoommatePayload(BaseModel):
    name: str


class ChorePayload(BaseModel):
    name: str
    frequency: str = "one-off"
    description: str = ""


class PreferencePayload(BaseModel):
    roommate_id: int
    chore_id: int
    week_start: str
    wtp_cents: int
    bid_cents: int


class LedgerRunPayload(BaseModel):
    week_start: str


@app.get("/")
def home():
    return {
        "name": "Choremarket API",
        "frontend": "http://127.0.0.1:5173",
        "state": "/api/state",
    }


@app.get("/api/state")
def api_state(week_start: str | None = None, advance_week: bool = False):
    selected_week = selected_week_from_query(week_start, advance_week)
    return {
        "week_start": selected_week,
        "roommates": [row_to_dict(row) for row in repository.all_roommates()],
        "active_roommates": [row_to_dict(row) for row in repository.active_roommates()],
        "chores": [row_to_dict(row) for row in repository.active_chores()],
        "known_weeks": repository.known_weeks(),
        "frequencies": ["monthly", "weekly", "one-off"],
    }


@app.get("/api/roommates")
def api_roommates():
    return {
        "roommates": [row_to_dict(row) for row in repository.all_roommates()],
        "active_roommates": [row_to_dict(row) for row in repository.active_roommates()],
    }


@app.post("/api/roommates")
def api_create_roommate(payload: RoommatePayload):
    repository.add_roommate(payload.name)
    return api_roommates()


@app.delete("/api/roommates/{roommate_id}")
def api_remove_roommate(roommate_id: int):
    repository.remove_roommate(roommate_id)
    return api_roommates()


@app.post("/api/roommates/examples")
def api_create_example_roommates():
    repository.add_example_roommates()
    return api_roommates()


@app.get("/api/chores")
def api_chores():
    return {
        "chores": [row_to_dict(row) for row in repository.active_chores()],
        "frequencies": ["monthly", "weekly", "one-off"],
    }


@app.post("/api/chores")
def api_create_chore(payload: ChorePayload):
    repository.add_chore(payload.name, payload.frequency, payload.description)
    return api_chores()


@app.patch("/api/chores/{chore_id}")
def api_update_chore(chore_id: int, payload: ChorePayload):
    repository.update_chore(
        chore_id,
        payload.name,
        payload.frequency,
        payload.description,
    )
    return api_chores()


@app.delete("/api/chores/{chore_id}")
def api_remove_chore(chore_id: int):
    repository.remove_chore(chore_id)
    return api_chores()


@app.get("/api/preferences")
def api_preferences(
    week_start: str | None = None,
    advance_week: bool = False,
):
    selected_week = selected_week_from_query(week_start, advance_week)
    roommates = repository.active_roommates()
    chores = repository.active_chores()
    effective = repository.effective_preferences_for_week(selected_week)

    preferences = []
    for chore in chores:
        for roommate in roommates:
            pref = effective[chore["id"]][roommate["id"]]
            preferences.append(
                {
                    "roommate_id": pref.roommate_id,
                    "chore_id": pref.chore_id,
                    "week_start": selected_week,
                    "wtp_cents": pref.wtp_cents,
                    "bid_cents": pref.bid_cents,
                    "source_week": pref.source_week,
                }
            )

    return {
        "week_start": selected_week,
        "roommates": [row_to_dict(row) for row in roommates],
        "chores": [row_to_dict(row) for row in chores],
        "preferences": preferences,
        "history": {
            str(roommate["id"]): [
                {
                    "week_start": row["week_start"],
                    "preferences": [row_to_dict(pref) for pref in row["preferences"]],
                }
                for row in repository.roommate_history(roommate["id"])
            ]
            for roommate in roommates
        },
        "known_weeks": repository.known_weeks(),
    }


@app.put("/api/preferences")
def api_save_preference(payload: PreferencePayload):
    week_start = repository.week_from_string(payload.week_start)
    if week_has_passed(week_start):
        raise HTTPException(
            status_code=403,
            detail="Preferences cannot be edited once the week has passed.",
        )
    repository.save_preferences(
        payload.roommate_id,
        week_start,
        {payload.chore_id: (payload.wtp_cents, payload.bid_cents)},
    )
    return {"ok": True}


@app.get("/api/ledger")
def api_ledger(
    week_start: str | None = None,
    advance_week: bool = False,
):
    selected_week = selected_week_from_query(week_start, advance_week)
    preview = repository.compute_week_ledger(selected_week)
    saved_rows = repository.saved_ledger_for_week(selected_week)
    return {
        "week_start": selected_week,
        "roommates": [row_to_dict(row) for row in repository.active_roommates()],
        "preview": [serialize_ledger_entry(entry) for entry in preview],
        "saved_rows": serialize_saved_ledger(saved_rows),
        "history_weeks": serialize_all_saved_weeks(),
        "known_weeks": repository.known_weeks(),
        "saved_weeks": [row_to_dict(row) for row in repository.saved_ledger_weeks()],
    }


@app.post("/api/ledger/run")
def api_save_ledger(payload: LedgerRunPayload):
    selected_week = repository.week_from_string(payload.week_start)
    repository.record_ledger_run(selected_week)
    return api_ledger(selected_week)


@app.get("/api/balances")
def api_balances():
    data = repository.overall_balances()
    return {
        "nets": [row_to_dict(row) for row in data["nets"]],
        "settlements": data["settlements"],
        "saved_weeks": [row_to_dict(row) for row in repository.saved_ledger_weeks()],
    }


@app.post("/api/test/reset-mock-data")
def api_reset_mock_data():
    repository.reset_mock_data()
    return {
        "ok": True,
        "state": api_state(),
        "balances": api_balances(),
    }


def selected_week_from_query(
    week_start: str | None,
    advance_week: bool,
) -> str:
    selected = date.fromisoformat(week_start) if week_start else upcoming_week()
    if advance_week:
        selected += timedelta(days=7)
    return selected.isoformat()


def week_has_passed(week_start: str) -> bool:
    return date.fromisoformat(week_start) < date.today()


def row_to_dict(row) -> dict[str, object]:
    return {key: row[key] for key in row.keys()}


def serialize_ledger_entry(entry) -> dict[str, object]:
    return {
        "chore": {
            "id": entry.chore.id,
            "name": entry.chore.name,
            "frequency": entry.chore.frequency,
        },
        "assignee": (
            {"id": entry.assignee.id, "name": entry.assignee.name}
            if entry.assignee
            else None
        ),
        "surplus_cents": entry.surplus_cents,
        "payments": [
            {"roommate_id": roommate_id, "amount_cents": amount_cents}
            for roommate_id, amount_cents in sorted(entry.payments.items())
        ],
        "preferences": [
            {
                "roommate_id": pref.roommate_id,
                "chore_id": pref.chore_id,
                "wtp_cents": pref.wtp_cents,
                "bid_cents": pref.bid_cents,
                "source_week": pref.source_week,
            }
            for pref in entry.preferences.values()
        ],
        "notes": entry.notes,
    }


def serialize_saved_ledger(rows) -> list[dict[str, object]]:
    saved = []
    for entry in group_saved_ledger(rows):
        sheet_details = parse_sheet_description(entry["chore_description"])
        saved.append(
            {
                "chore_name": entry["chore_name"],
                "week_start": entry["week_start"],
                "frequency": entry["frequency"],
                "description": entry["chore_description"],
                "sheet_frequency": sheet_details["frequency"] or entry["frequency"],
                "due_date": sheet_details["due_date"],
                "listed_cost": sheet_details["listed_cost"],
                "assignee_name": entry["assignee_name"],
                "surplus_cents": entry["surplus_cents"],
                "payments": [row_to_dict(payment) for payment in entry["payments"]],
            }
        )
    return saved


def parse_sheet_description(description: str) -> dict[str, str]:
    details = {"frequency": "", "due_date": "", "listed_cost": ""}
    if not description.startswith("Sheet frequency: "):
        return details

    parts = [part.strip().rstrip(".") for part in description.split(";")]
    for part in parts:
        if part.startswith("Sheet frequency: "):
            details["frequency"] = part.replace("Sheet frequency: ", "", 1)
        elif part.startswith("due: "):
            details["due_date"] = part.replace("due: ", "", 1)
        elif part.startswith("listed cost(s): "):
            details["listed_cost"] = part.replace("listed cost(s): ", "", 1)
    return details


def serialize_all_saved_weeks() -> list[dict[str, object]]:
    return [
        {
            "week_start": row["week_start"],
            "entries": serialize_saved_ledger(
                repository.saved_ledger_for_week(row["week_start"])
            ),
        }
        for row in repository.saved_ledger_weeks_ascending()
    ]


def group_saved_ledger(rows):
    grouped: dict[int, dict[str, object]] = {}
    for row in rows:
        entry = grouped.setdefault(
            row["entry_id"],
            {
                "week_start": row["week_start"],
                "chore_name": row["chore_name"],
                "chore_description": row["chore_description"],
                "frequency": row["frequency"],
                "assignee_name": row["assignee_name"],
                "surplus_cents": row["surplus_cents"],
                "payments": [],
            },
        )
        entry["payments"].append(row)
    return list(grouped.values())
