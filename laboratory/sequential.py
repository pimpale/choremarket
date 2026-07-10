"""Reverse-Vickrey procurement followed by several demand-side rules."""

from __future__ import annotations

from math import ceil

from .domain import ChoreDomain, Outcome, Profile
from .demand_lp import DemandLPSolution, solve_unrestricted_demand_lp
from .mechanism import Lottery, deterministic_lottery


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


class UnrestrictedDemandSequential:
    """Reverse Vickrey plus a full price-indexed randomized demand LP."""

    name = "lp_demand_vickrey"

    def __init__(
        self,
        domain: ChoreDomain,
        solver_name: str = "appsi_highs",
        *,
        name: str | None = None,
    ) -> None:
        if name is not None:
            self.name = name
        values = sorted({t.value for t in domain.types})
        prices = sorted({t.cost for t in domain.types})
        self.solutions: dict[float, DemandLPSolution] = {
            price: solve_unrestricted_demand_lp(
                domain.n,
                values,
                prices,
                price=price,
                solver_name=solver_name,
            )
            for price in prices
        }

    def run(self, reports: Profile) -> Lottery:
        n = len(reports)
        performer, _, price = procurement(reports)
        values = tuple(t.value for t in reports)
        demand = self.solutions[price].run(values)
        probabilities: dict[Outcome, float] = {}
        masses: dict[Outcome, tuple[float, ...]] = {}
        for funded, probability in demand.probabilities.items():
            outcome: Outcome = performer if funded else None
            transfers = list(demand.transfer_mass[funded])
            if funded:
                # Demand transfers collect price; procurement pays it to winner.
                transfers[performer] += price * probability
            probabilities[outcome] = probability
            masses[outcome] = tuple(transfers)
        return Lottery(
            probabilities=probabilities,
            transfer_mass=masses,
        )
