from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from fractions import Fraction
from typing import Iterable


@dataclass(frozen=True)
class Roommate:
    id: int
    name: str


@dataclass(frozen=True)
class Chore:
    id: int
    name: str


@dataclass(frozen=True)
class Preference:
    roommate_id: int
    chore_id: int
    wtp_cents: int
    bid_cents: int
    source_week: str | None = None


@dataclass(frozen=True)
class ChoreLedger:
    chore: Chore
    assignee: Roommate | None
    surplus_cents: int
    payments: dict[int, int]
    preferences: dict[int, Preference]
    notes: str


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


def flat_payout_payments(
    assignee_id: int,
    roommate_ids: Iterable[int],
    payout_cents: int,
) -> dict[int, int]:
    """Balanced transfer for a directly-entered (one-off) chore.

    The assignee receives ``payout_cents``; everyone else splits the cost so the
    payments sum to exactly zero. Positive amounts pay, negative amounts receive.
    """
    ids = list(roommate_ids)
    payments = {rid: 0 for rid in ids}
    if assignee_id not in payments:
        payments[assignee_id] = 0

    others = [rid for rid in payments if rid != assignee_id]
    if not others or payout_cents == 0:
        return payments

    base = payout_cents // len(others)
    remainder = payout_cents - base * len(others)
    for index, rid in enumerate(sorted(others)):
        payments[rid] = base + (1 if index < remainder else 0)
    payments[assignee_id] = -sum(payments[rid] for rid in others)
    return payments


def compute_chore_ledger(
    chore: Chore,
    roommates: Iterable[Roommate],
    preferences: dict[int, Preference],
    forced_assignee_id: int | None = None,
) -> ChoreLedger:
    """Assign a chore and compute the balanced AGV transfer.

    ``forced_assignee_id`` overrides the auto-pick (lowest bidder) so the ledger
    can be hand-edited while keeping the transfer math consistent.
    """
    people = list(roommates)
    if not people:
        return ChoreLedger(
            chore=chore,
            assignee=None,
            surplus_cents=0,
            payments={},
            preferences=preferences,
            notes="No active roommates.",
        )

    if len(people) == 1:
        assignee = people[0]
        return ChoreLedger(
            chore=chore,
            assignee=assignee,
            surplus_cents=0,
            payments={assignee.id: 0},
            preferences=preferences,
            notes="Single-roommate week; no transfer needed.",
        )

    forced = next((p for p in people if p.id == forced_assignee_id), None)
    assignee = forced or min(
        people,
        key=lambda roommate: (
            preferences[roommate.id].bid_cents,
            roommate.name.casefold(),
            roommate.id,
        ),
    )
    total_wtp = sum(preferences[person.id].wtp_cents for person in people)
    winning_bid = preferences[assignee.id].bid_cents
    surplus = total_wtp - winning_bid

    # A compact AGV-style transfer: choose the efficient chore doer, compute each
    # person's reported valuation of that outcome, then mean-center the externality
    # credits so the chore-level ledger balances exactly.
    valuations = {
        person.id: preferences[person.id].wtp_cents
        - (preferences[person.id].bid_cents if person.id == assignee.id else 0)
        for person in people
    }
    n = len(people)
    credits = {
        person.id: Fraction(
            sum(value for pid, value in valuations.items() if pid != person.id),
            n - 1,
        )
        for person in people
    }
    average_credit = sum(credits.values(), Fraction(0, 1)) / n
    raw_payments = {
        person.id: average_credit - credits[person.id]
        for person in people
    }
    payments = _fractions_to_balanced_cents(raw_payments)

    notes = "AGV-style externality transfer; positive amounts pay, negative amounts receive."
    return ChoreLedger(
        chore=chore,
        assignee=assignee,
        surplus_cents=surplus,
        payments=payments,
        preferences=preferences,
        notes=notes,
    )


def _fractions_to_balanced_cents(values: dict[int, Fraction]) -> dict[int, int]:
    ordered_ids = sorted(values)
    rounded: dict[int, int] = {}
    running = 0

    for person_id in ordered_ids[:-1]:
        fraction = values[person_id]
        value = int(round(float(fraction)))
        rounded[person_id] = value
        running += value

    rounded[ordered_ids[-1]] = -running
    return rounded
