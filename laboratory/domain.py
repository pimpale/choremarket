"""The finite chore-allocation domain shared by every laboratory mechanism."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from typing import Iterable, Iterator


@dataclass(frozen=True, order=True)
class Type:
    """A roommate's report: public-good value and private performance cost."""

    value: float
    cost: float

    def __post_init__(self) -> None:
        if self.value < 0 or self.cost < 0:
            raise ValueError("values and costs must be nonnegative")


Outcome = int | None  # None means no chore; an int is the performer index.
Profile = tuple[Type, ...]


@dataclass(frozen=True)
class ChoreDomain:
    """An exhaustive, finite direct-report domain."""

    n: int
    types: tuple[Type, ...]

    def __post_init__(self) -> None:
        if self.n < 2:
            raise ValueError("at least two roommates are required")
        if not self.types:
            raise ValueError("the type grid cannot be empty")
        if len(set(self.types)) != len(self.types):
            raise ValueError("type grid entries must be unique")

    @classmethod
    def rectangular(
        cls,
        n: int,
        value_levels: Iterable[float],
        cost_levels: Iterable[float],
    ) -> "ChoreDomain":
        values = tuple(float(v) for v in value_levels)
        costs = tuple(float(c) for c in cost_levels)
        return cls(n=n, types=tuple(Type(v, c) for v in values for c in costs))

    @property
    def outcomes(self) -> tuple[Outcome, ...]:
        return (None, *range(self.n))

    @property
    def profile_count(self) -> int:
        return len(self.types) ** self.n

    def profiles(self) -> Iterator[Profile]:
        return product(self.types, repeat=self.n)


def gross_utility(agent: int, true_type: Type, outcome: Outcome) -> float:
    if outcome is None:
        return 0.0
    return true_type.value - (true_type.cost if outcome == agent else 0.0)


def welfare(profile: Profile, outcome: Outcome) -> float:
    if outcome is None:
        return 0.0
    return sum(t.value for t in profile) - profile[outcome].cost


def efficient_outcome(profile: Profile, eligible: Iterable[int] | None = None) -> Outcome:
    """First-best outcome with deterministic lowest-index tie breaking.

    If ``eligible`` excludes agents, their types are omitted from the reduced
    economy's objective as well as from its performer set. This is the economy
    used by the sink mechanisms.
    """

    agents = tuple(range(len(profile))) if eligible is None else tuple(eligible)
    if not agents:
        return None
    performer = min(agents, key=lambda i: (profile[i].cost, i))
    reduced_welfare = sum(profile[i].value for i in agents) - profile[performer].cost
    return performer if reduced_welfare >= -1e-12 else None


def replace_type(profile: Profile, agent: int, report: Type) -> Profile:
    return profile[:agent] + (report,) + profile[agent + 1 :]


def nearest_level(value: float, levels: Iterable[float]) -> float:
    """Nearest grid level, ties broken toward the lower level."""

    return min(levels, key=lambda level: (abs(level - value), level))


def quantize_profile(
    profile: Profile,
    value_levels: Iterable[float],
    cost_levels: Iterable[float],
) -> Profile:
    values = tuple(map(float, value_levels))
    costs = tuple(map(float, cost_levels))
    return tuple(
        Type(nearest_level(t.value, values), nearest_level(t.cost, costs))
        for t in profile
    )
