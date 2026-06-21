from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import repository
from .db import init_db
from .weeks import current_week, upcoming_week

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
    join_date: str | None = None


class RoommateDatesPayload(BaseModel):
    join_date: str | None = None
    leave_date: str | None = None


class PaymentPayload(BaseModel):
    from_roommate_id: int
    to_roommate_id: int
    amount_cents: int
    note: str = ""
    paid_on: str | None = None


class RecurringChorePayload(BaseModel):
    name: str
    description: str = ""
    cadence: str = "weekly"


class PreferencePayload(BaseModel):
    roommate_id: int
    recurring_chore_id: int
    wtp_cents: int
    bid_cents: int


class InstancePreferencePayload(BaseModel):
    chore_instance_id: int
    roommate_id: int
    wtp_cents: int | None = None
    bid_cents: int | None = None


class OneOffPayload(BaseModel):
    name: str
    description: str = ""
    week_start: str
    assignee_id: int | None = None
    payout_cents: int = 0


class RecurringInstancePayload(BaseModel):
    recurring_chore_id: int


class NewRecurringInstancePayload(BaseModel):
    name: str
    description: str = ""
    cadence: str = "weekly"


class ManualOverridePayload(BaseModel):
    assignee_id: int
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


class SettingsPayload(BaseModel):
    mechanism: str


# Path to the built frontend (produced by `npm run build`). Present in the
# Docker image / production; absent in local dev where Vite serves the UI.
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


@app.get("/")
def home():
    if FRONTEND_DIST.is_dir():
        return FileResponse(FRONTEND_DIST / "index.html")
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
        "mechanism": repository.get_mechanism(),
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
# Settings (active transfer mechanism)
# --------------------------------------------------------------------------- #
@app.get("/api/settings")
def api_settings():
    return {"mechanism": repository.get_mechanism()}


@app.put("/api/settings")
def api_save_settings(payload: SettingsPayload):
    try:
        # Switching the mechanism re-derives every recurring instance under it.
        repository.set_mechanism(payload.mechanism)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    return {"mechanism": repository.get_mechanism()}


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
    repository.add_roommate(payload.name, payload.join_date)
    return api_roommates()


@app.patch("/api/roommates/{roommate_id}")
def api_update_roommate_dates(roommate_id: int, payload: RoommateDatesPayload):
    repository.update_roommate_dates(
        roommate_id, payload.join_date, payload.leave_date
    )
    return api_roommates()


@app.delete("/api/roommates/{roommate_id}")
def api_remove_roommate(roommate_id: int, leave_date: str | None = None):
    # "Removing" a roommate just closes their membership window (leave date).
    repository.remove_roommate(roommate_id, leave_date)
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
    try:
        repository.add_recurring_chore(
            payload.name,
            payload.description,
            payload.cadence,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    return api_recurring_chores()


@app.patch("/api/recurring-chores/{chore_id}")
def api_update_recurring_chore(chore_id: int, payload: RecurringChorePayload):
    try:
        repository.update_recurring_chore(
            chore_id,
            payload.name,
            payload.description,
            payload.cadence,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
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
    grid = repository.preferences_by_chore()
    preferences = [
        {
            "roommate_id": roommate_id,
            "recurring_chore_id": chore_id,
            "wtp_cents": pref["wtp_cents"],
            "bid_cents": pref["bid_cents"],
        }
        for chore_id, by_roommate in grid.items()
        for roommate_id, pref in by_roommate.items()
    ]
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
    # Nothing to recompute server-side: the client derives transfers live.
    return {"ok": True}


# --------------------------------------------------------------------------- #
# Ledger (instances)
# --------------------------------------------------------------------------- #
@app.get("/api/ledger")
def api_ledger(week_start: str | None = None, assignee_id: int | None = None):
    return {
        "current_week": current_week().isoformat(),
        "upcoming_week": upcoming_week().isoformat(),
        "mechanism": repository.get_mechanism(),
        "instances": repository.all_instances(week_start, assignee_id),
        "roommates": [row_to_dict(row) for row in repository.all_roommates()],
        "recurring_chores": [
            row_to_dict(row) for row in repository.active_recurring_chores()
        ],
        "preferences_by_chore": repository.preferences_by_chore(),
        "preferences_by_instance": repository.preferences_by_instance(),
        "recorded_payments": repository.list_roommate_payments(),
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


@app.put("/api/ledger/instance-preferences")
def api_save_instance_preference(payload: InstancePreferencePayload):
    try:
        repository.save_instance_preference(
            payload.chore_instance_id,
            payload.roommate_id,
            payload.wtp_cents,
            payload.bid_cents,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    return api_ledger()


@app.post("/api/ledger/instances/{instance_id}/recurring")
def api_convert_instance_to_recurring(
    instance_id: int,
    payload: RecurringInstancePayload,
):
    try:
        repository.convert_instance_to_recurring(
            instance_id,
            payload.recurring_chore_id,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    return api_ledger()


@app.post("/api/ledger/instances/{instance_id}/recurring/new")
def api_convert_instance_to_new_recurring(
    instance_id: int,
    payload: NewRecurringInstancePayload,
):
    try:
        repository.convert_instance_to_new_recurring(
            instance_id,
            payload.name,
            payload.description,
            payload.cadence,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    return api_ledger()


@app.post("/api/ledger/instances/{instance_id}/one-off")
def api_convert_instance_to_one_off(instance_id: int):
    try:
        repository.convert_recurring_to_one_off(instance_id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    return api_ledger()


@app.post("/api/ledger/instances/{instance_id}/manual-override")
def api_set_manual_override(
    instance_id: int,
    payload: ManualOverridePayload,
):
    try:
        repository.set_manual_override(
            instance_id,
            payload.assignee_id,
            payload.payout_cents,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
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


# --------------------------------------------------------------------------- #
# Recorded settle-up payments
# --------------------------------------------------------------------------- #
@app.post("/api/payments")
def api_add_payment(payload: PaymentPayload):
    try:
        repository.add_roommate_payment(
            payload.from_roommate_id,
            payload.to_roommate_id,
            payload.amount_cents,
            payload.note,
            payload.paid_on,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    return api_ledger()


@app.delete("/api/payments/{payment_id}")
def api_delete_payment(payment_id: int):
    repository.delete_roommate_payment(payment_id)
    return api_ledger()


@app.post("/api/test/reset-mock-data")
def api_reset_mock_data():
    repository.reset_mock_data()
    return {"ok": True, "ledger": api_ledger()}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def row_to_dict(row) -> dict[str, object]:
    return {key: row[key] for key in row.keys()}


# --------------------------------------------------------------------------- #
# Static frontend (production). Mounted last so every /api route above wins.
# --------------------------------------------------------------------------- #
if FRONTEND_DIST.is_dir():
    app.mount(
        "/assets",
        StaticFiles(directory=FRONTEND_DIST / "assets"),
        name="assets",
    )

    @app.get("/{full_path:path}")
    def spa_fallback(full_path: str):
        # Client-side routes (e.g. /balances) resolve to the SPA shell.
        return FileResponse(FRONTEND_DIST / "index.html")
