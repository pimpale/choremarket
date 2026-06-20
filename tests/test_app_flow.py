from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

import backend.app.db as db
import backend.app.repository as repo
from backend.app.mechanism import (
    Chore,
    Preference,
    Roommate,
    compute_chore_ledger,
    current_week,
    due_date_for,
    flat_payout_payments,
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
    # 2026-06-21 is a Sunday; the whole week maps back to it.
    sunday = date(2026, 6, 21)
    assert week_start_for(date(2026, 6, 21)) == sunday  # Sunday
    assert week_start_for(date(2026, 6, 24)) == sunday  # Wednesday
    assert week_start_for(date(2026, 6, 27)) == sunday  # Saturday
    # The next day rolls over to the following Sunday.
    assert week_start_for(date(2026, 6, 28)) == date(2026, 6, 28)


def test_due_date_is_saturday():
    assert due_date_for(date(2026, 6, 21)) == date(2026, 6, 27)


def test_current_and_upcoming_week():
    today = date(2026, 6, 24)  # Wednesday
    assert current_week(today) == date(2026, 6, 21)
    assert upcoming_week(today) == date(2026, 6, 28)


# --------------------------------------------------------------------------- #
# Mechanism invariants
# --------------------------------------------------------------------------- #
def _prefs(values: dict[int, tuple[int, int]]) -> dict[int, Preference]:
    return {
        rid: Preference(rid, 0, wtp, bid) for rid, (wtp, bid) in values.items()
    }


def test_payments_sum_to_zero_and_lowest_bidder_assigned():
    roommates = [Roommate(1, "Alex"), Roommate(2, "Blair"), Roommate(3, "Casey")]
    prefs = _prefs({1: (1500, 900), 2: (1200, 700), 3: (1800, 1100)})
    ledger = compute_chore_ledger(Chore(7, "Dishes"), roommates, prefs)

    assert ledger.assignee.id == 2  # lowest bid
    assert sum(ledger.payments.values()) == 0
    assert set(ledger.payments) == {1, 2, 3}


def test_lowest_bid_tie_broken_by_name_then_id():
    roommates = [Roommate(1, "Blair"), Roommate(2, "Alex")]
    prefs = _prefs({1: (1000, 500), 2: (1000, 500)})
    ledger = compute_chore_ledger(Chore(1, "Trash"), roommates, prefs)
    assert ledger.assignee.id == 2  # "Alex" < "Blair"


def test_single_and_empty_roommate_edges():
    solo = compute_chore_ledger(
        Chore(1, "Solo"), [Roommate(1, "Alex")], _prefs({1: (500, 200)})
    )
    assert solo.assignee.id == 1
    assert solo.payments == {1: 0}

    empty = compute_chore_ledger(Chore(1, "None"), [], {})
    assert empty.assignee is None
    assert empty.payments == {}


def test_flat_payout_is_balanced():
    payments = flat_payout_payments(1, [1, 2, 3, 4], 900)
    assert sum(payments.values()) == 0
    assert payments[1] == -900  # assignee receives the full payout
    assert payments[2] + payments[3] + payments[4] == 900


# --------------------------------------------------------------------------- #
# Spawning
# --------------------------------------------------------------------------- #
def test_spawn_week_is_idempotent(fresh_db):
    week = "2026-06-21"
    chores = repo.active_recurring_chores()
    first = repo.spawn_week(week)
    assert first == len(chores)

    second = repo.spawn_week(week)
    assert second == 0  # nothing new the second time

    instances = repo.all_instances(week_start=week)
    assert len(instances) == len(chores)
    # Due date is the Saturday of the Sunday-starting week.
    assert all(inst["due_date"] == "2026-06-27" for inst in instances)


def test_inactive_recurring_chore_is_not_spawned(fresh_db):
    chores = repo.active_recurring_chores()
    repo.remove_recurring_chore(chores[0]["id"])
    spawned = repo.spawn_week("2026-06-21")
    assert spawned == len(chores) - 1


def test_ensure_weeks_through_catches_up_and_is_idempotent(fresh_db):
    today = date(2026, 6, 24)  # current week 2026-06-21, upcoming 2026-06-28
    # Seed an old week so there is a gap to back-fill.
    repo.spawn_week("2026-05-31")

    repo.ensure_weeks_through(today)
    weeks = repo.known_instance_weeks()
    # Every Sunday from 2026-06-07 through 2026-06-28 should now exist.
    assert {"2026-05-31", "2026-06-07", "2026-06-14", "2026-06-21", "2026-06-28"} <= set(
        weeks
    )

    before = len(repo.all_instances())
    second = repo.ensure_weeks_through(today)
    assert second == 0
    assert len(repo.all_instances()) == before


# --------------------------------------------------------------------------- #
# Done / failed gating
# --------------------------------------------------------------------------- #
def test_only_done_instances_pay_out(fresh_db):
    week = "2026-06-21"
    # Non-trivial preferences so the AGV transfer is actually non-zero.
    roommates = repo.active_roommates()
    chore = repo.active_recurring_chores()[0]
    for offset, roommate in enumerate(roommates):
        repo.save_preference(roommate["id"], chore["id"], 1500, 600 + offset * 200)
    repo.spawn_week(week)
    instance = next(
        i
        for i in repo.all_instances(week_start=week)
        if i["recurring_chore_id"] == chore["id"]
    )

    # Pending: nothing counts yet.
    assert all(row["net_cents"] == 0 for row in repo.overall_balances()["nets"])

    repo.set_instance_status(instance["id"], "done")
    done_total = sum(abs(row["net_cents"]) for row in repo.overall_balances()["nets"])
    assert done_total > 0

    # Failing the same instance removes the payout again.
    repo.set_instance_status(instance["id"], "failed")
    assert all(row["net_cents"] == 0 for row in repo.overall_balances()["nets"])


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
# One-offs
# --------------------------------------------------------------------------- #
def test_one_off_instance_has_balanced_snapshot(fresh_db):
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
    assert sum(p["amount_cents"] for p in instance["payments"]) == 0


def test_update_one_off_payout_rebalances(fresh_db):
    roommate = repo.active_roommates()[0]
    instance_id = repo.add_one_off_instance(
        "Hang shelves", "", "2026-06-21", roommate["id"], 1000
    )
    repo.update_instance(instance_id, payout_cents=2400, name="Hang big shelves")
    instance = next(i for i in repo.all_instances() if i["id"] == instance_id)
    assert instance["name"] == "Hang big shelves"
    assert sum(p["amount_cents"] for p in instance["payments"]) == 0
    assignee_payment = next(
        p for p in instance["payments"] if p["roommate_id"] == roommate["id"]
    )
    assert assignee_payment["amount_cents"] == -2400  # assignee nets the new payout


def test_update_one_off_assignee_moves_payout(fresh_db):
    roommates = repo.active_roommates()
    instance_id = repo.add_one_off_instance(
        "Return packages", "", "2026-06-21", roommates[0]["id"], 900
    )
    repo.update_instance(instance_id, assignee_id=roommates[1]["id"])
    instance = next(i for i in repo.all_instances() if i["id"] == instance_id)
    assert instance["assignee_id"] == roommates[1]["id"]
    new_assignee_payment = next(
        p for p in instance["payments"] if p["roommate_id"] == roommates[1]["id"]
    )
    assert new_assignee_payment["amount_cents"] == -900
    assert sum(p["amount_cents"] for p in instance["payments"]) == 0


def test_update_instance_rejects_unknown_field(fresh_db):
    repo.spawn_week("2026-06-21")
    instance = repo.all_instances()[0]
    with pytest.raises(ValueError):
        repo.update_instance(instance["id"], color="red")


def test_recompute_future_weeks_only_touches_current_onward(fresh_db):
    today = date(2026, 6, 24)  # current week 2026-06-21
    repo.spawn_week("2026-06-07")  # past
    repo.spawn_week("2026-06-21")  # current
    repo.spawn_week("2026-06-28")  # upcoming
    chore_count = len(repo.active_recurring_chores())

    updated = repo.recompute_future_weeks(today)
    # Current + upcoming weeks recomputed; the past week is left alone.
    assert updated == chore_count * 2


def test_preferences_by_chore_shape(fresh_db):
    roommate = repo.active_roommates()[0]
    chore = repo.active_recurring_chores()[0]
    repo.save_preference(roommate["id"], chore["id"], 1234, 777)
    grid = repo.preferences_by_chore()
    assert grid[chore["id"]][roommate["id"]] == {"wtp_cents": 1234, "bid_cents": 777}


def test_recompute_refreshes_non_done_only(fresh_db):
    week = "2026-06-21"
    repo.spawn_week(week)
    instances = repo.all_instances(week_start=week)
    done_instance = instances[0]
    repo.set_instance_status(done_instance["id"], "done")

    roommates = repo.active_roommates()
    chore = repo.active_recurring_chores()[0]
    # Make one roommate dramatically cheaper so the assignee should change.
    for roommate in roommates:
        repo.save_preference(roommate["id"], chore["id"], 1000, 5000)
    cheap = roommates[-1]
    repo.save_preference(cheap["id"], chore["id"], 1000, 10)

    updated = repo.recompute_week(week)
    assert updated == len(instances) - 1  # the done one is skipped


# --------------------------------------------------------------------------- #
# Mock data
# --------------------------------------------------------------------------- #
def test_reset_mock_data_builds_expected_world(fresh_db):
    today = date(2026, 6, 24)
    repo.reset_mock_data(today)

    assert [r["name"] for r in repo.active_roommates()] == sorted(repo.MOCK_ROOMMATES)
    assert len(repo.active_recurring_chores()) == len(repo.MOCK_RECURRING_CHORES)

    # Three completed weeks + current + upcoming all exist.
    weeks = set(repo.known_instance_weeks())
    assert "2026-06-21" in weeks  # current
    assert "2026-06-28" in weeks  # upcoming

    # Completed weeks paid out -> non-trivial balances that net to zero.
    nets = repo.overall_balances()["nets"]
    assert sum(row["net_cents"] for row in nets) == 0
    assert any(row["net_cents"] != 0 for row in nets)

    # The one-off exists and there is at least one failure.
    instances = repo.all_instances()
    assert any(i["is_one_off"] for i in instances)
    assert any(i["status"] == "failed" for i in instances)
