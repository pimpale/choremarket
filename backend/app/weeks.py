from __future__ import annotations

from datetime import date, timedelta


def week_start_for(day: date) -> date:
    """Return the Sunday on or before ``day`` (weeks run Sunday -> Saturday)."""
    # date.weekday(): Mon=0 .. Sun=6. Days since the most recent Sunday:
    return day - timedelta(days=(day.weekday() + 1) % 7)


def due_date_for(week_start: date) -> date:
    """Return the Saturday that closes the Sunday-starting ``week_start`` week."""
    return week_start + timedelta(days=6)


def current_week(day: date | None = None) -> date:
    return week_start_for(day or date.today())


def upcoming_week(day: date | None = None) -> date:
    return current_week(day) + timedelta(days=7)
