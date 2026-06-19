from __future__ import annotations

from pathlib import Path

import backend.app.repository as repo
import backend.app.db as db
from backend.app.main import selected_week_from_query, week_has_passed


def test_seed_edit_increment_and_ledger_flow(tmp_path: Path) -> None:
    db.DB_PATH = tmp_path / "test.sqlite3"
    db.init_db()

    roommates = repo.active_roommates()
    assert [roommate["name"] for roommate in roommates] == ["Alex", "Blair", "Casey"]
    repo.add_roommate("Devon")
    repo.remove_roommate(roommates[0]["id"])
    repo.add_example_roommates()
    roommates = repo.active_roommates()
    assert [roommate["name"] for roommate in roommates] == [
        "Alex",
        "Blair",
        "Casey",
        "Devon",
    ]

    chores = repo.active_chores()
    chore_id = chores[0]["id"]
    repo.update_chore(chore_id, "Bathroom Reset", "monthly", "Deep clean test")
    updated = [chore for chore in repo.active_chores() if chore["id"] == chore_id][0]
    assert updated["name"] == "Bathroom Reset"
    assert updated["frequency"] == "monthly"
    assert updated["description"] == "Deep clean test"

    assert selected_week_from_query("2026-06-22", True) == "2026-06-29"
    assert week_has_passed("2026-04-25")
    assert not week_has_passed("2099-01-01")

    for roommate in roommates:
        repo.save_preferences(
            roommate["id"],
            "2026-06-22",
            {
                chore["id"]: (
                    1200 + roommate["id"] * 100,
                    600 + roommate["id"] * 50,
                )
                for chore in repo.active_chores()
            },
        )

    preview = repo.compute_week_ledger("2026-06-29")
    assert preview
    assert all(entry.assignee for entry in preview)

    repo.record_ledger_run("2026-06-29")
    balances = repo.overall_balances()
    assert balances["nets"]
    assert balances["settlements"]

    repo.reset_mock_data()
    assert [roommate["name"] for roommate in repo.active_roommates()] == [
        "Blaine",
        "Emerson",
        "Govind",
        "Matthew",
        "Nathan",
    ]
    assert {chore["frequency"] for chore in repo.active_chores()} == {
        "monthly",
        "weekly",
        "one-off",
    }
    assert len(repo.active_chores()) >= 18
    assert repo.known_weeks()
    saved_weeks = [row["week_start"] for row in repo.saved_ledger_weeks_ascending()]
    assert saved_weeks[0] == "2026-04-25"
    assert saved_weeks[-1] == "2026-06-13"
