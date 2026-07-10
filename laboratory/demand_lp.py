"""Unrestricted finite-domain demand mechanisms at a fixed procurement price."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from itertools import combinations_with_replacement, product
from math import factorial
from typing import Iterable, Mapping

from .solver import pyomo, solve


ValueProfile = tuple[float, ...]


@dataclass(frozen=True)
class DemandLottery:
    """Funding probabilities and probability-weighted demand transfers."""

    probabilities: Mapping[bool, float]
    transfer_mass: Mapping[bool, tuple[float, ...]]

    def expected_transfer(self, agent: int) -> float:
        return sum(mass[agent] for mass in self.transfer_mass.values())

    def expected_utility(self, agent: int, true_value: float) -> float:
        return self.probabilities.get(True, 0.0) * true_value + self.expected_transfer(agent)


@dataclass(frozen=True)
class DemandLPSolution:
    price: float
    n: int
    value_levels: tuple[float, ...]
    table: Mapping[ValueProfile, DemandLottery]
    worst_case_regret: float
    average_welfare: float
    transfer_bound: float
    max_conditional_transfer: float

    def run(self, values: ValueProfile) -> DemandLottery:
        try:
            return self.table[values]
        except KeyError as exc:
            raise ValueError("WTP profile is outside the solved demand grid") from exc


def _canonicalize(profile: ValueProfile) -> tuple[ValueProfile, tuple[int, ...]]:
    order = sorted(range(len(profile)), key=lambda i: (profile[i], i))
    old_to_new = [0] * len(profile)
    for new_position, old_agent in enumerate(order):
        old_to_new[old_agent] = new_position
    return tuple(profile[old_agent] for old_agent in order), tuple(old_to_new)


def _orbit_size(profile: ValueProfile) -> int:
    size = factorial(len(profile))
    for count in Counter(profile).values():
        size //= factorial(count)
    return size


def _winning_cost_weights(
    n: int,
    cost_levels: tuple[float, ...],
    price: float,
) -> dict[float, float]:
    """Distribution of the lowest cost conditional on the second-lowest price."""

    counts: Counter[float] = Counter()
    for costs in product(cost_levels, repeat=n):
        ordered = sorted(costs)
        if abs(ordered[1] - price) <= 1e-12:
            counts[ordered[0]] += 1
    if not counts:
        raise ValueError(f"price {price} cannot arise on the supplied cost grid")
    total = sum(counts.values())
    return {cost: count / total for cost, count in counts.items()}


def solve_unrestricted_demand_lp(
    n: int,
    value_levels: Iterable[float],
    cost_levels: Iterable[float],
    price: float,
    solver_name: str = "appsi_highs",
    transfer_bound: float | None = None,
    regret_tolerance: float = 1e-7,
) -> DemandLPSolution:
    """Solve the full DSIC-in-expectation demand LP at one fixed price.

    Demand transfer ``d_i`` excludes the procurement payment to the performer.
    Conditional balance is ``sum_i d_i=-price`` when funded and zero otherwise.
    Balanced side transfers are allowed when the chore is not funded; this is
    necessary for FaltingsFair to lie inside the feasible mechanism class.
    """

    if n < 2:
        raise ValueError("at least two demand agents are required")
    values = tuple(sorted({float(value) for value in value_levels}))
    costs = tuple(sorted({float(cost) for cost in cost_levels}))
    if not values or not costs:
        raise ValueError("value and cost grids cannot be empty")

    pyo = pyomo()
    all_profiles = tuple(product(values, repeat=n))
    profiles = tuple(combinations_with_replacement(values, n))
    profile_index = {profile: index for index, profile in enumerate(profiles)}
    profile_weights = tuple(_orbit_size(profile) / len(all_profiles) for profile in profiles)
    winning_cost_weights = _winning_cost_weights(n, costs, price)
    winning_costs = tuple(winning_cost_weights)
    scale = max((*values, *costs, price))
    bound = transfer_bound if transfer_bound is not None else max(1.0, 4 * n * scale)
    if bound <= 0:
        raise ValueError("transfer_bound must be positive")

    model = pyo.ConcreteModel()
    model.P = pyo.RangeSet(0, len(profiles) - 1)
    model.F = pyo.RangeSet(0, 1)  # 0=no chore, 1=fund
    model.I = pyo.RangeSet(0, n - 1)
    model.C = pyo.RangeSet(0, len(winning_costs) - 1)
    model.x = pyo.Var(model.P, model.F, domain=pyo.NonNegativeReals, bounds=(0.0, 1.0))
    model.d = pyo.Var(model.P, model.F, model.I, domain=pyo.Reals)
    model.regret = pyo.Var(domain=pyo.NonNegativeReals)

    model.simplex = pyo.Constraint(
        model.P,
        rule=lambda m, p: sum(m.x[p, funded] for funded in m.F) == 1,
    )

    def balance_rule(m, p, funded):
        required = -price * m.x[p, funded] if funded else 0.0
        return sum(m.d[p, funded, i] for i in m.I) == required

    model.balance = pyo.Constraint(model.P, model.F, rule=balance_rule)
    model.transfer_upper = pyo.Constraint(
        model.P,
        model.F,
        model.I,
        rule=lambda m, p, funded, i: m.d[p, funded, i] <= bound * m.x[p, funded],
    )
    model.transfer_lower = pyo.Constraint(
        model.P,
        model.F,
        model.I,
        rule=lambda m, p, funded, i: m.d[p, funded, i] >= -bound * m.x[p, funded],
    )

    def expected_utility(m, p: int, agent: int, true_value: float):
        return (
            m.x[p, 1] * true_value
            + sum(m.d[p, funded, agent] for funded in m.F)
        )

    deviation_keys = []
    deviation_target: dict[tuple[int, int, int], tuple[int, int]] = {}
    for p, profile in enumerate(profiles):
        representatives = [
            i for i in range(n) if i == 0 or profile[i] != profile[i - 1]
        ]
        for i in representatives:
            for report_index, alternate in enumerate(values):
                if alternate == profile[i]:
                    continue
                key = (p, i, report_index)
                deviation_keys.append(key)
                deviated = profile[:i] + (alternate,) + profile[i + 1 :]
                canonical, old_to_new = _canonicalize(deviated)
                deviation_target[key] = (profile_index[canonical], old_to_new[i])
    model.D = pyo.Set(initialize=deviation_keys, dimen=3)

    def dsic_rule(m, p, i, report_index):
        target_p, target_i = deviation_target[(p, i, report_index)]
        true_value = profiles[p][i]
        return expected_utility(m, p, i, true_value) >= expected_utility(
            m, target_p, target_i, true_value
        )

    model.dsic = pyo.Constraint(model.D, rule=dsic_rule)

    stabilizers = []
    for p, profile in enumerate(profiles):
        for left in range(n - 1):
            if profile[left] == profile[left + 1]:
                stabilizers.append((p, left))
    model.S = pyo.Set(initialize=stabilizers, dimen=2)

    def stabilizer_rule(m, p, left, funded, i):
        if i == left:
            target_i = left + 1
        elif i == left + 1:
            target_i = left
        else:
            target_i = i
        return m.d[p, funded, i] == m.d[p, funded, target_i]

    model.stabilizer = pyo.Constraint(
        model.S, model.F, model.I, rule=stabilizer_rule
    )

    def regret_rule(m, p, cost_index):
        surplus = sum(profiles[p]) - winning_costs[cost_index]
        first_best = max(0.0, surplus)
        return m.regret >= first_best - m.x[p, 1] * surplus

    model.regret_bounds = pyo.Constraint(model.P, model.C, rule=regret_rule)

    average_welfare = sum(
        profile_weights[p]
        * winning_cost_weights[winning_costs[c]]
        * model.x[p, 1]
        * (sum(profiles[p]) - winning_costs[c])
        for p in model.P
        for c in model.C
    )
    model.primary_objective = pyo.Objective(expr=model.regret, sense=pyo.minimize)
    persistent_solver = solve(model, solver_name)
    best_regret = float(pyo.value(model.regret))

    model.primary_objective.deactivate()
    tie_tolerance = regret_tolerance * max(1.0, abs(best_regret), price)
    model.regret_tie = pyo.Constraint(expr=model.regret <= best_regret + tie_tolerance)
    model.secondary_objective = pyo.Objective(expr=average_welfare, sense=pyo.maximize)
    solve(model, solver_name, solver=persistent_solver)

    best_average = float(pyo.value(average_welfare))
    model.secondary_objective.deactivate()
    model.average_tie = pyo.Constraint(
        expr=average_welfare >= best_average - tie_tolerance
    )
    model.abs_d = pyo.Var(model.P, model.F, model.I, domain=pyo.NonNegativeReals)
    model.abs_d_positive = pyo.Constraint(
        model.P,
        model.F,
        model.I,
        rule=lambda m, p, funded, i: m.abs_d[p, funded, i] >= m.d[p, funded, i],
    )
    model.abs_d_negative = pyo.Constraint(
        model.P,
        model.F,
        model.I,
        rule=lambda m, p, funded, i: m.abs_d[p, funded, i] >= -m.d[p, funded, i],
    )
    model.transfer_objective = pyo.Objective(
        expr=sum(model.abs_d[p, funded, i] for p in model.P for funded in model.F for i in model.I),
        sense=pyo.minimize,
    )
    solve(model, solver_name, solver=persistent_solver)

    reduced_table: dict[ValueProfile, DemandLottery] = {}
    for p, profile in enumerate(profiles):
        raw_probabilities: dict[bool, float] = {}
        raw_masses: dict[bool, tuple[float, ...]] = {}
        for funded in (0, 1):
            probability = max(0.0, float(pyo.value(model.x[p, funded])))
            if probability <= 1e-10:
                continue
            mass = [float(pyo.value(model.d[p, funded, i])) for i in range(n)]
            required = -price * probability if funded else 0.0
            mass[-1] += required - sum(mass)
            raw_probabilities[bool(funded)] = probability
            raw_masses[bool(funded)] = tuple(mass)
        total_probability = sum(raw_probabilities.values())
        reduced_table[profile] = DemandLottery(
            probabilities={
                funded: probability / total_probability
                for funded, probability in raw_probabilities.items()
            },
            transfer_mass={
                funded: tuple(value / total_probability for value in raw_masses[funded])
                for funded in raw_probabilities
            },
        )

    table: dict[ValueProfile, DemandLottery] = {}
    for profile in all_profiles:
        canonical, old_to_new = _canonicalize(profile)
        reduced = reduced_table[canonical]
        masses: dict[bool, tuple[float, ...]] = {}
        for funded, canonical_mass in reduced.transfer_mass.items():
            mass = [0.0] * n
            for old_agent, new_position in enumerate(old_to_new):
                mass[old_agent] = canonical_mass[new_position]
            masses[funded] = tuple(mass)
        table[profile] = DemandLottery(
            probabilities=dict(reduced.probabilities),
            transfer_mass=masses,
        )

    max_conditional = max(
        abs(transfer / lottery.probabilities[funded])
        for lottery in table.values()
        for funded, mass in lottery.transfer_mass.items()
        for transfer in mass
    )
    return DemandLPSolution(
        price=price,
        n=n,
        value_levels=values,
        table=table,
        worst_case_regret=float(pyo.value(model.regret)),
        average_welfare=float(pyo.value(average_welfare)),
        transfer_bound=bound,
        max_conditional_transfer=max_conditional,
    )


def audit_demand_solution(solution: DemandLPSolution) -> dict[str, float]:
    """Exhaustively certify demand-stage WTP IC and conditional cost recovery."""

    max_gain = 0.0
    max_balance_error = 0.0
    for profile, truthful in solution.table.items():
        for funded, probability in truthful.probabilities.items():
            required = -solution.price * probability if funded else 0.0
            max_balance_error = max(
                max_balance_error,
                abs(sum(truthful.transfer_mass[funded]) - required),
            )
        for i, true_value in enumerate(profile):
            truthful_utility = truthful.expected_utility(i, true_value)
            for alternate in solution.value_levels:
                if alternate == profile[i]:
                    continue
                deviated_profile = profile[:i] + (alternate,) + profile[i + 1 :]
                deviating_utility = solution.run(deviated_profile).expected_utility(
                    i, true_value
                )
                max_gain = max(max_gain, deviating_utility - truthful_utility)
    return {
        "max_wtp_deviation_gain": max_gain,
        "max_conditional_balance_error": max_balance_error,
    }
