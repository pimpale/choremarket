"""Common representation for deterministic and randomized mechanisms."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol

from .domain import ChoreDomain, Outcome, Profile, gross_utility, quantize_profile, welfare


@dataclass(frozen=True)
class Lottery:
    """Expected mechanism result at one report profile.

    ``transfer_mass[o][i]`` is the allocation probability times the transfer
    received by i conditional on o. FaltingsFair instead supplies its symmetric
    report-contingent expected transfer directly.
    """

    probabilities: Mapping[Outcome, float]
    transfer_mass: Mapping[Outcome, tuple[float, ...]]
    expected_transfers_override: tuple[float, ...] | None = None

    def expected_transfer(self, agent: int) -> float:
        if self.expected_transfers_override is not None:
            return self.expected_transfers_override[agent]
        return sum(ts[agent] for ts in self.transfer_mass.values())

    def expected_transfers(self) -> tuple[float, ...]:
        if self.expected_transfers_override is not None:
            return self.expected_transfers_override
        if not self.transfer_mass:
            return ()
        n = len(next(iter(self.transfer_mass.values())))
        return tuple(self.expected_transfer(i) for i in range(n))

    def expected_utility(self, agent: int, true_type) -> float:
        return sum(
            probability * gross_utility(agent, true_type, outcome)
            for outcome, probability in self.probabilities.items()
        ) + self.expected_transfer(agent)

    def expected_welfare(self, true_profile: Profile) -> float:
        return sum(
            probability * welfare(true_profile, outcome)
            for outcome, probability in self.probabilities.items()
        )


class Mechanism(Protocol):
    name: str

    def run(self, reports: Profile) -> Lottery: ...


class TabularMechanism:
    def __init__(self, name: str, domain: ChoreDomain, table: Mapping[Profile, Lottery]):
        self.name = name
        self.domain = domain
        self.table = dict(table)

    def run(self, reports: Profile) -> Lottery:
        try:
            return self.table[reports]
        except KeyError as exc:
            raise ValueError("report profile is outside this mechanism's finite domain") from exc


class QuantizedReports:
    """Quantize raw reports at the door of a finite-domain mechanism.

    The deployment interface of tabular mechanisms: participants report
    continuous types, the mechanism sees the nearest grid levels. Wrapping
    lets one mechanism list serve raw-type and grid mechanisms alike.
    """

    def __init__(self, mechanism, value_levels, cost_levels) -> None:
        self.name = mechanism.name
        self.mechanism = mechanism
        self.value_levels = tuple(map(float, value_levels))
        self.cost_levels = tuple(map(float, cost_levels))

    def run(self, reports: Profile) -> Lottery:
        return self.mechanism.run(
            quantize_profile(reports, self.value_levels, self.cost_levels)
        )


def deterministic_lottery(outcome: Outcome, transfers: tuple[float, ...]) -> Lottery:
    return Lottery(probabilities={outcome: 1.0}, transfer_mass={outcome: transfers})


def allocation_only_lottery(outcome: Outcome, n: int) -> Lottery:
    return deterministic_lottery(outcome, (0.0,) * n)
