"""Exhaustive finite-domain incentive, balance, and participation audits."""

from __future__ import annotations

from dataclasses import dataclass
from math import inf

from .domain import ChoreDomain, Outcome, Profile, gross_utility, replace_type
from .mechanism import Mechanism


@dataclass(frozen=True)
class IncentiveViolation:
    gain: float
    profile: Profile
    agent: int
    misreport_value: float
    misreport_cost: float
    truthful_utility: float
    deviating_utility: float


@dataclass(frozen=True)
class AuditReport:
    max_joint_deviation_gain: float
    max_value_only_gain: float
    max_cost_only_gain: float
    worst_joint_deviation: IncentiveViolation | None
    max_expected_budget_error: float
    max_conditional_budget_error: float
    conditional_outcomes_audited: bool
    min_ex_ante_utility: float
    ex_ante_utility_by_agent: tuple[float, ...]
    min_expected_utility: float
    min_conditional_utility: float | None

    def is_dsic_in_expectation(self, tolerance: float = 1e-7) -> bool:
        return self.max_joint_deviation_gain <= tolerance

    def is_expected_budget_balanced(self, tolerance: float = 1e-7) -> bool:
        return self.max_expected_budget_error <= tolerance


def audit_mechanism(
    mechanism: Mechanism,
    domain: ChoreDomain,
    tolerance: float = 1e-9,
) -> AuditReport:
    max_joint = max_value = max_cost = 0.0
    worst: IncentiveViolation | None = None
    max_expected_bb = max_conditional_bb = 0.0
    min_expected_ir = inf
    min_conditional_ir = inf
    conditional_outcomes_audited = True
    ex_ante_totals = [0.0] * domain.n
    profile_count = 0

    for profile in domain.profiles():
        profile_count += 1
        truthful = mechanism.run(profile)
        expected_transfers = truthful.expected_transfers()
        max_expected_bb = max(max_expected_bb, abs(sum(expected_transfers)))

        if truthful.expected_transfers_override is None:
            for outcome, probability in truthful.probabilities.items():
                if probability <= tolerance:
                    continue
                masses = truthful.transfer_mass.get(outcome, (0.0,) * domain.n)
                max_conditional_bb = max(
                    max_conditional_bb,
                    abs(sum(masses)) / probability,
                )
                for i in range(domain.n):
                    conditional_transfer = masses[i] / probability
                    min_conditional_ir = min(
                        min_conditional_ir,
                        gross_utility(i, profile[i], outcome) + conditional_transfer,
                    )
        else:
            conditional_outcomes_audited = False

        for i in range(domain.n):
            truthful_utility = truthful.expected_utility(i, profile[i])
            ex_ante_totals[i] += truthful_utility
            min_expected_ir = min(min_expected_ir, truthful_utility)
            for misreport in domain.types:
                if misreport == profile[i]:
                    continue
                deviation = mechanism.run(replace_type(profile, i, misreport))
                deviating_utility = deviation.expected_utility(i, profile[i])
                gain = deviating_utility - truthful_utility
                if misreport.cost == profile[i].cost:
                    max_value = max(max_value, gain)
                if misreport.value == profile[i].value:
                    max_cost = max(max_cost, gain)
                if gain > max_joint + tolerance:
                    max_joint = gain
                    worst = IncentiveViolation(
                        gain=gain,
                        profile=profile,
                        agent=i,
                        misreport_value=misreport.value,
                        misreport_cost=misreport.cost,
                        truthful_utility=truthful_utility,
                        deviating_utility=deviating_utility,
                    )

    ex_ante_utility = tuple(total / profile_count for total in ex_ante_totals)
    return AuditReport(
        max_joint_deviation_gain=max(0.0, max_joint),
        max_value_only_gain=max(0.0, max_value),
        max_cost_only_gain=max(0.0, max_cost),
        worst_joint_deviation=worst,
        max_expected_budget_error=max_expected_bb,
        max_conditional_budget_error=max_conditional_bb,
        conditional_outcomes_audited=conditional_outcomes_audited,
        min_ex_ante_utility=min(ex_ante_utility),
        ex_ante_utility_by_agent=ex_ante_utility,
        min_expected_utility=min_expected_ir,
        min_conditional_utility=(min_conditional_ir if conditional_outcomes_audited else None),
    )
