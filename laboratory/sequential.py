"""Reverse-Vickrey procurement followed by several demand-side rules."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations, product
from math import ceil
from typing import Iterable

from .domain import ChoreDomain, Outcome, Profile
from .mechanism import Lottery, deterministic_lottery
from .solver import pyomo, solve


def procurement(reports: Profile) -> tuple[int, float, float]:
    """Return performer, lowest bid, and second-lowest Vickrey price."""

    order = sorted(range(len(reports)), key=lambda i: (reports[i].cost, i))
    return order[0], reports[order[0]].cost, reports[order[1]].cost


class EqualShareMajoritySequential:
    name = "equal_split_vickrey_majority"

    def run(self, reports: Profile) -> Lottery:
        n = len(reports)
        performer, _, price = procurement(reports)
        share = price / n
        supporters = sum(t.value + 1e-12 >= share for t in reports)
        if supporters < ceil((n + 1) / 2):
            return deterministic_lottery(None, (0.0,) * n)
        transfers = [-share] * n
        transfers[performer] += price
        return deterministic_lottery(performer, tuple(transfers))


def _public_project_vcg_charges(
    net_values: tuple[float, ...],
    agents: tuple[int, ...],
) -> dict[int, float]:
    """Pivot charges for a build/no-build project in a reduced economy."""

    funds = sum(net_values[i] for i in agents) >= -1e-12
    charges: dict[int, float] = {}
    for i in agents:
        others = tuple(j for j in agents if j != i)
        others_sum = sum(net_values[j] for j in others)
        welfare_without_i = max(0.0, others_sum)
        welfare_at_choice = others_sum if funds else 0.0
        charges[i] = welfare_without_i - welfare_at_choice
    return charges


class EqualSplitVickreyFaltingsFair:
    """Reverse Vickrey supply plus equal-split FaltingsFair demand.

    The second-lowest bid is the fixed project price. Equal shares finance the
    performer. For demand, each agent's net value of funding is v_i-price/n;
    FaltingsFair excludes one demand report uniformly for the allocation and
    applies its symmetric expected VCG charge/rebate formula. Side transfers
    sum to zero and are spread identically across the realized lottery branches.
    """

    name = "equal_split_vickrey_faltings_fair"

    def run(self, reports: Profile) -> Lottery:
        n = len(reports)
        performer, _, price = procurement(reports)
        net_values = tuple(report.value - price / n for report in reports)
        branch_funds: dict[int, bool] = {}
        reduced_charges: dict[int, dict[int, float]] = {}

        for excluded in range(n):
            agents = tuple(i for i in range(n) if i != excluded)
            branch_funds[excluded] = sum(net_values[i] for i in agents) >= -1e-12
            reduced_charges[excluded] = _public_project_vcg_charges(net_values, agents)

        fair_charges = []
        for i in range(n):
            expected_own_charge = sum(
                reduced_charges[excluded].get(i, 0.0)
                for excluded in range(n)
                if excluded != i
            ) / n
            rebate = sum(reduced_charges[i].values()) / n
            fair_charges.append(expected_own_charge - rebate)
        fair_transfers = tuple(-charge for charge in fair_charges)

        probabilities: dict[Outcome, float] = {}
        masses: dict[Outcome, list[float]] = {}
        for excluded, funds in branch_funds.items():
            outcome: Outcome = performer if funds else None
            probability = 1.0 / n
            probabilities[outcome] = probabilities.get(outcome, 0.0) + probability
            mass = masses.setdefault(outcome, [0.0] * n)
            base = [0.0] * n
            if funds:
                base = [-price / n] * n
                base[performer] += price
            for i in range(n):
                mass[i] += probability * (base[i] + fair_transfers[i])

        return Lottery(
            probabilities=probabilities,
            transfer_mass={outcome: tuple(values) for outcome, values in masses.items()},
        )


@dataclass(frozen=True)
class DemandBranch:
    name: str
    charges: tuple[float, ...]
    voters: tuple[int, ...]
    quota: int

    def funds(self, values: tuple[float, ...]) -> bool:
        return sum(values[i] + 1e-12 >= self.charges[i] for i in self.voters) >= self.quota


def demand_branch_library(n: int, price: float) -> tuple[DemandBranch, ...]:
    """Interpretable DSIC and exactly balanced fixed-price demand branches."""

    equal = tuple(price / n for _ in range(n))
    branches = [
        DemandBranch("equal_unanimity", equal, tuple(range(n)), n),
        DemandBranch("equal_majority", equal, tuple(range(n)), ceil((n + 1) / 2)),
        DemandBranch("equal_two_thirds", equal, tuple(range(n)), ceil(2 * n / 3)),
    ]
    for k in range(1, n + 1):
        for sponsors in combinations(range(n), k):
            sponsor_set = set(sponsors)
            charges = tuple(price / k if i in sponsor_set else 0.0 for i in range(n))
            branches.append(DemandBranch(f"sponsors[{','.join(map(str, sponsors))}]", charges, sponsors, k))
    # Several definitions coincide for small n or price zero.
    unique = { (b.charges, b.voters, b.quota): b for b in branches }
    return tuple(unique.values())


def optimize_demand_mixture(
    n: int,
    value_levels: Iterable[float],
    price: float,
    winning_cost_levels: Iterable[float] | None = None,
    solver_name: str = "appsi_highs",
) -> tuple[tuple[DemandBranch, float], ...]:
    """Minimax-regret mixture over truthful posted-price demand branches.

    The demand rule observes only ``price``. Its objective is robust over every
    possible winning effort cost no larger than that second price.
    """

    pyo = pyomo()
    values = tuple(tuple(map(float, p)) for p in product(value_levels, repeat=n))
    possible_costs = tuple(
        cost
        for cost in (
            tuple(map(float, winning_cost_levels))
            if winning_cost_levels is not None
            else (price,)
        )
        if cost <= price + 1e-12
    )
    scenarios = tuple((value_profile, cost) for value_profile in values for cost in possible_costs)
    branches = demand_branch_library(n, price)
    model = pyo.ConcreteModel()
    model.B = pyo.RangeSet(0, len(branches) - 1)
    model.P = pyo.RangeSet(0, len(scenarios) - 1)
    model.weight = pyo.Var(model.B, domain=pyo.NonNegativeReals)
    model.regret = pyo.Var(domain=pyo.NonNegativeReals)
    model.simplex = pyo.Constraint(expr=sum(model.weight[b] for b in model.B) == 1)

    def regret_rule(m, p):
        value_profile, winning_cost = scenarios[p]
        surplus = sum(value_profile) - winning_cost
        optimal = max(0.0, surplus)
        achieved = sum(
            m.weight[b] * (surplus if branches[b].funds(value_profile) else 0.0)
            for b in m.B
        )
        return m.regret >= optimal - achieved

    model.regret_bounds = pyo.Constraint(model.P, rule=regret_rule)
    average = sum(
        model.weight[b]
        * sum(
            (sum(vs) - cost if branches[b].funds(vs) else 0.0)
            for vs, cost in scenarios
        )
        / len(scenarios)
        for b in model.B
    )
    model.primary_objective = pyo.Objective(expr=model.regret, sense=pyo.minimize)
    solve(model, solver_name)
    best_regret = float(pyo.value(model.regret))
    model.primary_objective.deactivate()
    # HiGHS' feasibility tolerance is absolute; leave a scale-aware margin when
    # reusing the primary optimum as a lexicographic constraint.
    tie_tolerance = 1e-6 * max(1.0, abs(best_regret), price)
    model.regret_tie = pyo.Constraint(expr=model.regret <= best_regret + tie_tolerance)
    model.secondary_objective = pyo.Objective(expr=average, sense=pyo.maximize)
    solve(model, solver_name)
    return tuple(
        (branches[b], float(pyo.value(model.weight[b])))
        for b in model.B
        if float(pyo.value(model.weight[b])) > 1e-9
    )


class OptimizedDemandSequential:
    """Reverse Vickrey plus a price-indexed minimax posted-price lottery."""

    name = "lp_demand_vickrey"

    def __init__(self, domain: ChoreDomain, solver_name: str = "appsi_highs") -> None:
        values = sorted({t.value for t in domain.types})
        prices = sorted({t.cost for t in domain.types})
        self.mixtures = {
            price: optimize_demand_mixture(
                domain.n,
                values,
                price,
                winning_cost_levels=prices,
                solver_name=solver_name,
            )
            for price in prices
        }

    def run(self, reports: Profile) -> Lottery:
        n = len(reports)
        performer, _, price = procurement(reports)
        values = tuple(t.value for t in reports)
        probabilities: dict[Outcome, float] = {None: 0.0, performer: 0.0}
        masses: dict[Outcome, list[float]] = {None: [0.0] * n, performer: [0.0] * n}
        for branch, weight in self.mixtures[price]:
            outcome: Outcome = performer if branch.funds(values) else None
            probabilities[outcome] += weight
            if outcome is not None:
                transfers = [-charge for charge in branch.charges]
                transfers[performer] += price
                for i in range(n):
                    masses[outcome][i] += weight * transfers[i]
        return Lottery(
            probabilities={o: p for o, p in probabilities.items() if p > 1e-12},
            transfer_mass={o: tuple(masses[o]) for o, p in probabilities.items() if p > 1e-12},
        )
