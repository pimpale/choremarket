"""Reverse-Vickrey procurement followed by several demand-side rules."""

from __future__ import annotations

import multiprocessing
import os
from concurrent.futures import ProcessPoolExecutor
from itertools import combinations
from math import ceil
from typing import Literal

from .domain import ChoreDomain, Outcome, Profile, nearest_level
from .demand_lp import DemandLottery, DemandLPSolution, solve_unrestricted_demand_lp
from .mechanism import Lottery, deterministic_lottery
from .profiling import timed


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


GuoVariant = Literal["asymptotic", "finite_n"]


def _guo_h3_extended(a: float, b: float, threshold: float) -> float:
    """Dollar-scaled h3** from Guo (2019), Section 5.

    ``threshold`` is the paper's ``t``. Keeping this expression in dollars
    avoids dividing by a zero Vickrey price and makes the homogeneity of the
    normalized public-project formula explicit.
    """

    capped_a = min(a, threshold)
    capped_b = min(b, threshold)
    return (
        a
        - capped_a
        + b
        - capped_b
        + max(capped_a + capped_b, 2 * threshold / 3)
        + max(capped_a + capped_b, threshold) / 2
        - max(capped_a, capped_b, 2 * threshold / 3) / 2
        - threshold / 6
    )


def _guo_f(
    a: float,
    b: float,
    z: float,
    price: float,
    variant: GuoVariant,
) -> float:
    """Dollar-scaled dimension-reduced ``f(a, b, z)`` from the paper."""

    if z >= price:
        return (a + b) / 2 + z / 3

    threshold = price - z
    if variant == "finite_n":
        # Theorem 3: no bounded-precision assumption; ratio (n+1)/(2n).
        return z / 3 + _guo_h3_extended(a, b, threshold) / 2

    # Theorem 1 / Equation (6). C appears only in the theorem's performance
    # guarantee; neither this formula nor its implementation requires C.
    joint = max(a + b, threshold)
    return (
        z / 3
        + joint / 3
        + (joint - max(b, threshold)) / 6 * (a > 0)
        + (joint - max(a, threshold)) / 6 * (b > 0)
        + threshold / 3 * (a > 0 and b > 0)
    )


def guo_redistribution_h(
    values: tuple[float, ...],
    price: float,
    variant: GuoVariant,
) -> tuple[float, ...]:
    """Return the anonymous Groves ``h(theta_-i)`` values in dollars.

    This is Equation (3)'s average over every ordered draw of three distinct
    agents. ``f`` is symmetric in its first two arguments, so summing each
    unordered pair once and doubling it gives the O(n^3) implementation.
    """

    n = len(values)
    if n < 3:
        raise ValueError("Guo's three-agent dimension reduction requires n >= 3")
    if price < 0 or any(value < 0 for value in values):
        raise ValueError("values and the public-project price must be nonnegative")

    total = sum(values)
    scale = 6 / (n * (n - 2))
    h_values = []
    for i in range(n):
        others = (j for j in range(n) if j != i)
        f_sum = 0.0
        for j, k in combinations(others, 2):
            # max removes only floating-point cancellation below zero; all
            # primitive values are nonnegative.
            z = max(0.0, total - values[i] - values[j] - values[k])
            f_sum += _guo_f(values[j], values[k], z, price, variant)
        h_values.append(scale * f_sum)
    return tuple(h_values)


class GuoPublicProjectSequential:
    """Reverse Vickrey procurement plus Guo's 2019 public-project rule.

    The second-lowest cost bid supplies the project price that the paper
    normalizes to one. The demand rule builds efficiently at that fixed price.
    Its Groves transfers are weakly budget balanced: unlike FaltingsFair, it
    can retain a surplus, but it never requires a subsidy.
    """

    variant: GuoVariant
    name: str

    def run(self, reports: Profile) -> Lottery:
        n = len(reports)
        performer, _, price = procurement(reports)
        values = tuple(report.value for report in reports)
        h_values = guo_redistribution_h(values, price, self.variant)
        funds = sum(values) >= price - 1e-12

        if funds:
            # In the paper, not building preserves each agent's price/n
            # endowment. Subtract that constant endowment to express transfers
            # in the laboratory's zero-at-no-chore convention, then pay the
            # reverse-auction winner the Vickrey price.
            transfers = [
                sum(values) - values[i] - h_values[i] - price / n
                for i in range(n)
            ]
            transfers[performer] += price
            return deterministic_lottery(performer, tuple(transfers))

        transfers = tuple(
            price * (n - 1) / n - h_values[i] for i in range(n)
        )
        return deterministic_lottery(None, transfers)


class GuoAsymptoticSequential(GuoPublicProjectSequential):
    """The asymptotically optimal mechanism from Guo (2019), Theorem 1."""

    name = "equal_split_vickrey_guo_2019_asymptotic"
    variant: GuoVariant = "asymptotic"


class GuoFiniteNSequential(GuoPublicProjectSequential):
    """Guo (2019), Theorem 3, which needs no precision assumption."""

    name = "equal_split_vickrey_guo_2019_finite_n"
    variant: GuoVariant = "finite_n"


def _single_threaded_solver() -> None:
    # Worker initializer: many small single-threaded LPs in parallel beat a
    # few multithreaded ones fighting over the same cores.
    os.environ["CHOREMARKET_LP_THREADS"] = "1"


def _solve_price_indexed_demand(
    n: int,
    values: tuple[float, ...],
    prices: tuple[float, ...],
    solver_name: str,
    label: str,
) -> dict[float, DemandLPSolution]:
    with timed(f"demand LP solves ({label})"):
        # The per-price LPs are independent; solve them across processes.
        # Threads would not help: Pyomo model construction is Python-bound.
        if len(prices) < 4:
            return {
                price: solve_unrestricted_demand_lp(
                    n, values, prices, price, solver_name
                )
                for price in prices
            }
        workers = min(len(prices), os.cpu_count() or 1)
        # Spawn, not fork: the parent has already run multithreaded HiGHS
        # solves, and forked children inherit its native thread state broken.
        with ProcessPoolExecutor(
            max_workers=workers,
            mp_context=multiprocessing.get_context("spawn"),
            initializer=_single_threaded_solver,
        ) as pool:
            futures = {
                price: pool.submit(
                    solve_unrestricted_demand_lp, n, values, prices, price, solver_name
                )
                for price in prices
            }
            return {price: future.result() for price, future in futures.items()}


def _compose_supply_demand(
    demand: DemandLottery,
    performer: int,
    price: float,
    n: int,
    residual: float = 0.0,
) -> Lottery:
    """Attach the procurement payment (and any price-rounding residual)."""

    probabilities: dict[Outcome, float] = {}
    masses: dict[Outcome, tuple[float, ...]] = {}
    for funded, probability in demand.probabilities.items():
        outcome: Outcome = performer if funded else None
        transfers = list(demand.transfer_mass[funded])
        if funded:
            # Demand transfers collect the rounded price; the equal residual
            # charge tops that up to the exact price paid to the winner.
            for i in range(n):
                transfers[i] -= residual * probability / n
            transfers[performer] += price * probability
        probabilities[outcome] = probability
        masses[outcome] = tuple(transfers)
    return Lottery(probabilities=probabilities, transfer_mass=masses)


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
        values = tuple(sorted({t.value for t in domain.types}))
        prices = tuple(sorted({t.cost for t in domain.types}))
        self.solutions = _solve_price_indexed_demand(
            domain.n, values, prices, solver_name, self.name
        )

    def run(self, reports: Profile) -> Lottery:
        n = len(reports)
        performer, _, price = procurement(reports)
        values = tuple(t.value for t in reports)
        demand = self.solutions[price].run(values)
        return _compose_supply_demand(demand, performer, price, n)


class RawBidDemandSequential:
    """Reverse Vickrey on raw cost bids plus the price-indexed demand LP.

    The deployment interface of the sequential composition: performer
    selection and the Vickrey price use unquantized bids, so no supply-side
    welfare is lost to a report grid. WTP reports are quantized internally to
    the demand grid, and the demand lottery is looked up at the nearest level
    of a configurable price grid. The performer receives the exact Vickrey
    price; the gap to the rounded price is charged equally in the funded
    branch, keeping the composition exactly budget balanced and supply-side
    DSIC, while demand incentives are within (price step)/(2n) of exact.
    """

    name = "lp_demand_vickrey_raw_bids"

    def __init__(
        self,
        n: int,
        value_levels: tuple[float, ...],
        price_levels: tuple[float, ...],
        solver_name: str = "appsi_highs",
        *,
        name: str | None = None,
    ) -> None:
        if name is not None:
            self.name = name
        self.value_levels = tuple(sorted({float(v) for v in value_levels}))
        prices = tuple(sorted({float(p) for p in price_levels}))
        self.solutions = _solve_price_indexed_demand(
            n, self.value_levels, prices, solver_name, self.name
        )

    def run(self, reports: Profile) -> Lottery:
        n = len(reports)
        performer, _, price = procurement(reports)
        rounded = nearest_level(price, self.solutions)
        values = tuple(nearest_level(t.value, self.value_levels) for t in reports)
        demand = self.solutions[rounded].run(values)
        return _compose_supply_demand(
            demand, performer, price, n, residual=price - rounded
        )
