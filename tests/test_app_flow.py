from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

import backend.app.db as db
import backend.app.repository as repo
from backend.app.weeks import (
    current_week,
    due_date_for,
    upcoming_week,
    week_start_for,
)


@pytest.fixture
def fresh_db(tmp_path: Path):
    db.DB_PATH = tmp_path / "test.sqlite3"
    db.init_db()
    return db.DB_PATH


# --------------------------------------------------------------------------- #
# Week math (Sunday -> Saturday)
# --------------------------------------------------------------------------- #
def test_week_start_lands_on_sunday():
    sunday = date(2026, 6, 21)
    assert week_start_for(date(2026, 6, 21)) == sunday  # Sunday
    assert week_start_for(date(2026, 6, 24)) == sunday  # Wednesday
    assert week_start_for(date(2026, 6, 27)) == sunday  # Saturday
    assert week_start_for(date(2026, 6, 28)) == date(2026, 6, 28)


def test_due_date_is_saturday():
    assert due_date_for(date(2026, 6, 21)) == date(2026, 6, 27)


def test_current_and_upcoming_week():
    today = date(2026, 6, 24)  # Wednesday
    assert current_week(today) == date(2026, 6, 21)
    assert upcoming_week(today) == date(2026, 6, 28)


# --------------------------------------------------------------------------- #
# Settings (active mechanism). The economics live on the client; the backend
# only stores which mechanism is selected.
# --------------------------------------------------------------------------- #
def test_mechanism_defaults_to_agv_and_round_trips(fresh_db):
    assert repo.get_mechanism() == "agv"
    assert repo.set_mechanism("vcg") == "vcg"
    assert repo.get_mechanism() == "vcg"


def test_set_mechanism_rejects_unknown(fresh_db):
    with pytest.raises(ValueError):
        repo.set_mechanism("bogus")


def test_initial_roommates_match_mock_roommates(fresh_db):
    assert [r["name"] for r in repo.active_roommates()] == sorted(repo.MOCK_ROOMMATES)


# --------------------------------------------------------------------------- #
# Spawning (raw instances only -- no assignee/transfer computed here)
# --------------------------------------------------------------------------- #
def test_spawn_week_is_idempotent_and_raw(fresh_db):
    week = "2026-06-21"
    chores = repo.active_recurring_chores()
    assert repo.spawn_week(week) == len(chores)
    assert repo.spawn_week(week) == 0  # nothing new the second time

    instances = repo.all_instances(week_start=week)
    assert len(instances) == len(chores)
    assert all(inst["due_date"] == "2026-06-27" for inst in instances)
    # Recurring rows carry no server-side assignee or payout.
    assert all(inst["assignee_id"] is None for inst in instances)
    assert all(inst["payout_cents"] == 0 for inst in instances)
    assert all(inst["status"] == "pending" for inst in instances)


def test_inactive_recurring_chore_is_not_spawned(fresh_db):
    chores = repo.active_recurring_chores()
    repo.remove_recurring_chore(chores[0]["id"])
    assert repo.spawn_week("2026-06-21") == len(chores) - 1


def test_recurring_chore_cadence_controls_spawning(fresh_db):
    weekly_count = len(repo.active_recurring_chores())
    repo.add_recurring_chore("Change air filter", "", "monthly")
    repo.add_recurring_chore("Clean oven after party", "", "ad-hoc")

    assert repo.spawn_week("2026-06-21") == weekly_count
    june_rows = repo.all_instances(week_start="2026-06-21")
    assert all(row["name"] != "Change air filter" for row in june_rows)
    assert all(row["name"] != "Clean oven after party" for row in june_rows)

    assert repo.spawn_week("2026-06-28") == weekly_count + 1
    july_rows = repo.all_instances(week_start="2026-06-28")
    assert any(row["name"] == "Change air filter" for row in july_rows)
    assert all(row["name"] != "Clean oven after party" for row in july_rows)


def test_recurring_chore_rejects_unknown_cadence(fresh_db):
    with pytest.raises(ValueError):
        repo.add_recurring_chore("Mystery chore", "", "sometimes")


def test_ensure_weeks_through_catches_up_and_is_idempotent(fresh_db):
    today = date(2026, 6, 24)
    repo.spawn_week("2026-05-31")  # seed an old week so there is a gap

    repo.ensure_weeks_through(today)
    weeks = set(repo.known_instance_weeks())
    assert {"2026-05-31", "2026-06-07", "2026-06-14", "2026-06-21", "2026-06-28"} <= weeks

    before = len(repo.all_instances())
    assert repo.ensure_weeks_through(today) == 0
    assert len(repo.all_instances()) == before


# --------------------------------------------------------------------------- #
# Status
# --------------------------------------------------------------------------- #
def test_status_is_single_valued(fresh_db):
    repo.spawn_week("2026-06-21")
    instance = repo.all_instances()[0]
    repo.set_instance_status(instance["id"], "done")
    assert repo.all_instances()[0]["status"] == "done"
    repo.set_instance_status(instance["id"], "failed")
    assert repo.all_instances()[0]["status"] == "failed"
    with pytest.raises(ValueError):
        repo.set_instance_status(instance["id"], "bogus")


# --------------------------------------------------------------------------- #
# One-offs + raw instance edits
# --------------------------------------------------------------------------- #
def test_one_off_stores_assignee_and_payout(fresh_db):
    roommate = repo.active_roommates()[0]
    instance_id = repo.add_one_off_instance(
        name="Fix the door",
        description="squeaky hinge",
        week_start="2026-06-21",
        assignee_id=roommate["id"],
        payout_cents=1200,
    )
    instance = next(i for i in repo.all_instances() if i["id"] == instance_id)
    assert instance["is_one_off"] is True
    assert instance["recurring_chore_id"] is None
    assert instance["assignee_id"] == roommate["id"]
    assert instance["payout_cents"] == 1200


def test_one_off_can_convert_to_duplicate_recurring_instance(fresh_db):
    week = "2026-06-21"
    repo.spawn_week(week)
    chore = repo.active_recurring_chores()[0]
    roommate = repo.active_roommates()[0]
    instance_id = repo.add_one_off_instance(
        name="Temporary row",
        description="",
        week_start=week,
        assignee_id=roommate["id"],
        payout_cents=1200,
    )

    repo.convert_instance_to_recurring(instance_id, chore["id"])

    matches = [
        instance
        for instance in repo.all_instances(week_start=week)
        if instance["recurring_chore_id"] == chore["id"]
    ]
    converted = next(instance for instance in matches if instance["id"] == instance_id)
    assert len(matches) == 2
    assert converted["is_one_off"] is False
    assert converted["name"] == chore["name"]
    assert converted["description"] == chore["description"]
    assert converted["assignee_id"] is None
    assert converted["payout_cents"] == 0


def test_one_off_can_convert_to_new_recurring_instance(fresh_db):
    instance_id = repo.add_one_off_instance(
        name="Clean pantry",
        description="",
        week_start="2026-06-21",
        assignee_id=None,
        payout_cents=0,
    )

    repo.convert_instance_to_new_recurring(instance_id, "Clean pantry", "Sort shelves", "monthly")

    chore = next(chore for chore in repo.active_recurring_chores() if chore["name"] == "Clean pantry")
    instance = next(instance for instance in repo.all_instances() if instance["id"] == instance_id)
    assert chore["cadence"] == "monthly"
    assert instance["recurring_chore_id"] == chore["id"]
    assert instance["description"] == "Sort shelves"
    assert instance["manual_override"] is False


def test_one_off_can_become_manual_override(fresh_db):
    roommate = repo.active_roommates()[0]
    instance_id = repo.add_one_off_instance(
        name="Emergency cleanup",
        description="",
        week_start="2026-06-21",
        assignee_id=None,
        payout_cents=0,
    )
    repo.save_instance_preference(instance_id, roommate["id"], 1200, 900)

    repo.set_manual_override(instance_id, roommate["id"], 2500)

    instance = next(instance for instance in repo.all_instances() if instance["id"] == instance_id)
    assert instance["manual_override"] is True
    assert instance["assignee_id"] == roommate["id"]
    assert instance["payout_cents"] == 2500
    assert repo.preferences_by_instance()[instance_id][roommate["id"]] == {"wtp_cents": None, "bid_cents": None}


def test_one_off_instance_preferences_round_trip(fresh_db):
    roommate = repo.active_roommates()[0]
    instance_id = repo.add_one_off_instance(
        name="Temporary row",
        description="",
        week_start="2026-06-21",
        assignee_id=None,
        payout_cents=0,
    )

    prefs = repo.preferences_by_instance()
    assert prefs[instance_id][roommate["id"]] == {"wtp_cents": None, "bid_cents": None}

    repo.save_instance_preference(instance_id, roommate["id"], 1200, None)
    assert repo.preferences_by_instance()[instance_id][roommate["id"]] == {
        "wtp_cents": 1200,
        "bid_cents": None,
    }


def test_update_instance_edits_raw_fields(fresh_db):
    roommates = repo.active_roommates()
    instance_id = repo.add_one_off_instance(
        "Hang shelves", "", "2026-06-21", roommates[0]["id"], 1000
    )
    repo.update_instance(
        instance_id,
        name="Hang big shelves",
        payout_cents=2400,
        assignee_id=roommates[1]["id"],
    )
    instance = next(i for i in repo.all_instances() if i["id"] == instance_id)
    assert instance["name"] == "Hang big shelves"
    assert instance["payout_cents"] == 2400
    assert instance["assignee_id"] == roommates[1]["id"]


def test_update_instance_rejects_unknown_field(fresh_db):
    repo.spawn_week("2026-06-21")
    instance = repo.all_instances()[0]
    with pytest.raises(ValueError):
        repo.update_instance(instance["id"], color="red")


def test_update_instance_rejects_invalid_status(fresh_db):
    repo.spawn_week("2026-06-21")
    instance = repo.all_instances()[0]
    with pytest.raises(ValueError):
        repo.update_instance(instance["id"], status="bogus")


def test_delete_instance(fresh_db):
    instance_id = repo.add_one_off_instance("Temp", "", "2026-06-21", None, 0)
    repo.delete_instance(instance_id)
    assert all(i["id"] != instance_id for i in repo.all_instances())


# --------------------------------------------------------------------------- #
# Preferences grid (everything the client needs to derive transfers)
# --------------------------------------------------------------------------- #
def test_preferences_by_chore_shape(fresh_db):
    roommate = repo.active_roommates()[0]
    chore = repo.active_recurring_chores()[0]
    repo.save_preference(roommate["id"], chore["id"], 1234, 777)
    grid = repo.preferences_by_chore()
    assert grid[chore["id"]][roommate["id"]] == {"wtp_cents": 1234, "bid_cents": 777}
    # Unset pairs default to zero rather than going missing.
    other = repo.active_roommates()[1]
    assert grid[chore["id"]][other["id"]] == {"wtp_cents": 0, "bid_cents": 0}


# --------------------------------------------------------------------------- #
# Mock data
# --------------------------------------------------------------------------- #
def test_reset_mock_data_builds_expected_world(fresh_db):
    today = date(2026, 6, 24)
    repo.reset_mock_data(today)

    assert [r["name"] for r in repo.active_roommates()] == sorted(repo.MOCK_ROOMMATES)
    assert len(repo.active_recurring_chores()) == len(repo.MOCK_RECURRING_CHORES)

    weeks = set(repo.known_instance_weeks())
    assert "2026-06-21" in weeks  # current
    assert "2026-06-28" in weeks  # upcoming

    instances = repo.all_instances()
    assert any(i["is_one_off"] for i in instances)
    assert any(i["status"] == "failed" for i in instances)
    assert any(i["status"] == "done" for i in instances)


# --------------------------------------------------------------------------- #
# Membership windows (join/leave) + recorded payments
# --------------------------------------------------------------------------- #
def test_remove_roommate_sets_leave_date(fresh_db):
    roommate = repo.active_roommates()[0]
    repo.remove_roommate(roommate["id"], "2026-06-15")
    row = next(r for r in repo.all_roommates() if r["id"] == roommate["id"])
    assert row["leave_date"] == "2026-06-15"
    assert row["active"] == 0


def test_update_roommate_dates_tracks_active(fresh_db):
    roommate = repo.active_roommates()[0]
    repo.update_roommate_dates(roommate["id"], "2026-06-01", None)
    row = next(r for r in repo.all_roommates() if r["id"] == roommate["id"])
    assert row["join_date"] == "2026-06-01"
    assert row["leave_date"] is None
    assert row["active"] == 1
    # A leave date in the past marks them inactive.
    repo.update_roommate_dates(roommate["id"], "2026-06-01", "2020-01-01")
    row = next(r for r in repo.all_roommates() if r["id"] == roommate["id"])
    assert row["active"] == 0


def test_preferences_grid_includes_departed_roommates(fresh_db):
    departed = repo.active_roommates()[0]
    chore = repo.active_recurring_chores()[0]
    repo.remove_roommate(departed["id"])
    grid = repo.preferences_by_chore()
    # Departed members stay in the grid so the client can recompute their history.
    assert departed["id"] in grid[chore["id"]]


def test_roommate_payments_crud(fresh_db):
    rs = repo.active_roommates()
    pid = repo.add_roommate_payment(rs[0]["id"], rs[1]["id"], 500, "pizza", "2026-06-10")
    payments = repo.list_roommate_payments()
    assert len(payments) == 1
    assert payments[0]["amount_cents"] == 500
    assert payments[0]["note"] == "pizza"
    assert payments[0]["from_name"] == rs[0]["name"]
    repo.delete_roommate_payment(pid)
    assert repo.list_roommate_payments() == []


def test_roommate_payment_validation(fresh_db):
    rs = repo.active_roommates()
    with pytest.raises(ValueError):
        repo.add_roommate_payment(rs[0]["id"], rs[0]["id"], 500)  # same roommate
    with pytest.raises(ValueError):
        repo.add_roommate_payment(rs[0]["id"], rs[1]["id"], 0)  # non-positive


# --------------------------------------------------------------------------- #
# Recurring -> manual-override ("sell to roommate") / one-off conversions
# --------------------------------------------------------------------------- #
def test_recurring_can_be_sold_to_roommate(fresh_db):
    repo.spawn_week("2026-06-21")
    instance = next(i for i in repo.all_instances() if not i["is_one_off"])
    buyer = repo.active_roommates()[0]
    repo.set_manual_override(instance["id"], buyer["id"], 1500)
    after = next(i for i in repo.all_instances() if i["id"] == instance["id"])
    assert after["is_one_off"] is True
    assert after["manual_override"] is True
    assert after["assignee_id"] == buyer["id"]
    assert after["payout_cents"] == 1500


def test_recurring_can_convert_to_one_off(fresh_db):
    repo.spawn_week("2026-06-21")
    instance = next(i for i in repo.all_instances() if not i["is_one_off"])
    name = instance["name"]
    rms = repo.active_roommates()
    repo.save_preference(rms[0]["id"], instance["recurring_chore_id"], 1500, 900)

    repo.convert_recurring_to_one_off(instance["id"])
    after = next(i for i in repo.all_instances() if i["id"] == instance["id"])
    assert after["is_one_off"] is True
    assert after["manual_override"] is False
    assert after["name"] == name  # name preserved

    # The chore's wtp/bid are snapshotted onto the new one-off (unset -> 0/0).
    snapshot = repo.preferences_by_instance()[instance["id"]]
    assert snapshot[rms[0]["id"]] == {"wtp_cents": 1500, "bid_cents": 900}
    assert snapshot[rms[1]["id"]] == {"wtp_cents": 0, "bid_cents": 0}

    # A one-off can't be re-converted to a one-off.
    with pytest.raises(ValueError):
        repo.convert_recurring_to_one_off(instance["id"])
