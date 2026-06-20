from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import date

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import repository
from .db import init_db
from .mechanism import current_week, upcoming_week

SPAWNER_INTERVAL_SECONDS = 3600


async def _weekly_spawner() -> None:
    """Background task: keep the ledger filled through the upcoming week."""
    while True:
        await asyncio.sleep(SPAWNER_INTERVAL_SECONDS)
        try:
            repository.ensure_weeks_through()
        except Exception:  # pragma: no cover - defensive, keep the loop alive
            pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    repository.ensure_weeks_through()
    task = asyncio.create_task(_weekly_spawner())
    try:
        yield
    finally:
        task.cancel()


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


class RecurringChorePayload(BaseModel):
    name: str
    description: str = ""


class PreferencePayload(BaseModel):
    roommate_id: int
    recurring_chore_id: int
    wtp_cents: int
    bid_cents: int


class OneOffPayload(BaseModel):
    name: str
    description: str = ""
    week_start: str
    assignee_id: int | None = None
    payout_cents: int = 0


class InstanceUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    due_date: str | None = None
    assignee_id: int | None = None
    payout_cents: int | None = None
    status: str | None = None


class WeekPayload(BaseModel):
    week_start: str


@app.get("/")
def home():
    return {
        "name": "Choremarket API",
        "frontend": "http://127.0.0.1:5173",
        "ledger": "/api/ledger",
    }


@app.get("/api/state")
def api_state():
    return {
        "current_week": current_week().isoformat(),
        "upcoming_week": upcoming_week().isoformat(),
        "roommates": [row_to_dict(row) for row in repository.all_roommates()],
        "active_roommates": [
            row_to_dict(row) for row in repository.active_roommates()
        ],
        "recurring_chores": [
            row_to_dict(row) for row in repository.active_recurring_chores()
        ],
        "weeks": repository.known_instance_weeks(),
    }


# --------------------------------------------------------------------------- #
# Roommates
# --------------------------------------------------------------------------- #
@app.get("/api/roommates")
def api_roommates():
    return {
        "roommates": [row_to_dict(row) for row in repository.all_roommates()],
        "active_roommates": [
            row_to_dict(row) for row in repository.active_roommates()
        ],
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


# --------------------------------------------------------------------------- #
# Recurring chores
# --------------------------------------------------------------------------- #
@app.get("/api/recurring-chores")
def api_recurring_chores():
    return {
        "recurring_chores": [
            row_to_dict(row) for row in repository.active_recurring_chores()
        ],
    }


@app.post("/api/recurring-chores")
def api_create_recurring_chore(payload: RecurringChorePayload):
    repository.add_recurring_chore(payload.name, payload.description)
    return api_recurring_chores()


@app.patch("/api/recurring-chores/{chore_id}")
def api_update_recurring_chore(chore_id: int, payload: RecurringChorePayload):
    repository.update_recurring_chore(chore_id, payload.name, payload.description)
    return api_recurring_chores()


@app.delete("/api/recurring-chores/{chore_id}")
def api_remove_recurring_chore(chore_id: int):
    repository.remove_recurring_chore(chore_id)
    return api_recurring_chores()


# --------------------------------------------------------------------------- #
# Preferences (recurring-chore WTP/bid)
# --------------------------------------------------------------------------- #
@app.get("/api/preferences")
def api_preferences():
    roommates = repository.active_roommates()
    chores = repository.active_recurring_chores()
    grid = repository.preferences_grid()
    preferences = []
    for roommate in roommates:
        for chore in chores:
            pref = grid[roommate["id"]][chore["id"]]
            preferences.append(
                {
                    "roommate_id": pref.roommate_id,
                    "recurring_chore_id": pref.chore_id,
                    "wtp_cents": pref.wtp_cents,
                    "bid_cents": pref.bid_cents,
                }
            )
    return {
        "roommates": [row_to_dict(row) for row in roommates],
        "recurring_chores": [row_to_dict(row) for row in chores],
        "preferences": preferences,
    }


@app.put("/api/preferences")
def api_save_preference(payload: PreferencePayload):
    repository.save_preference(
        payload.roommate_id,
        payload.recurring_chore_id,
        payload.wtp_cents,
        payload.bid_cents,
    )
    # Keep upcoming, not-yet-done ledger rows in sync automatically.
    repository.recompute_future_weeks()
    return {"ok": True}


# --------------------------------------------------------------------------- #
# Ledger (instances)
# --------------------------------------------------------------------------- #
@app.get("/api/ledger")
def api_ledger(week_start: str | None = None, assignee_id: int | None = None):
    instances = repository.all_instances(week_start, assignee_id)
    this_week = current_week().isoformat()
    next_week = upcoming_week().isoformat()
    for instance in instances:
        instance["week_class"] = week_class(instance, this_week, next_week)
    return {
        "current_week": this_week,
        "upcoming_week": next_week,
        "instances": instances,
        "roommates": [row_to_dict(row) for row in repository.active_roommates()],
        "recurring_chores": [
            row_to_dict(row) for row in repository.active_recurring_chores()
        ],
        "preferences_by_chore": repository.preferences_by_chore(),
        "weeks": repository.known_instance_weeks(),
    }


@app.post("/api/ledger/instances")
def api_add_one_off(payload: OneOffPayload):
    repository.add_one_off_instance(
        payload.name,
        payload.description,
        repository.week_from_string(payload.week_start),
        payload.assignee_id,
        payload.payout_cents,
    )
    return api_ledger()


@app.patch("/api/ledger/instances/{instance_id}")
def api_update_instance(instance_id: int, payload: InstanceUpdate):
    changes = payload.model_dump(exclude_unset=True)
    try:
        repository.update_instance(instance_id, **changes)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    return api_ledger()


@app.delete("/api/ledger/instances/{instance_id}")
def api_delete_instance(instance_id: int):
    repository.delete_instance(instance_id)
    return api_ledger()


@app.post("/api/ledger/spawn")
def api_spawn(payload: WeekPayload | None = None):
    if payload and payload.week_start:
        repository.spawn_week(repository.week_from_string(payload.week_start))
    else:
        repository.spawn_week(upcoming_week().isoformat())
    return api_ledger()


@app.post("/api/ledger/recompute")
def api_recompute(payload: WeekPayload):
    repository.recompute_week(repository.week_from_string(payload.week_start))
    return api_ledger()


# --------------------------------------------------------------------------- #
# Balances
# --------------------------------------------------------------------------- #
@app.get("/api/balances")
def api_balances():
    data = repository.overall_balances()
    return {
        "nets": [row_to_dict(row) for row in data["nets"]],
        "settlements": data["settlements"],
    }


@app.post("/api/test/reset-mock-data")
def api_reset_mock_data():
    repository.reset_mock_data()
    return {"ok": True, "ledger": api_ledger(), "balances": api_balances()}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def week_class(instance: dict, this_week: str, next_week: str) -> str:
    if instance["status"] == "done":
        return "done"
    if instance["week_start"] == this_week:
        return "current"
    if instance["week_start"] == next_week:
        return "upcoming"
    return "other"


def row_to_dict(row) -> dict[str, object]:
    return {key: row[key] for key in row.keys()}
