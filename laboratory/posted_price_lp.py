"""LP experiments over randomized posted-price chore mechanisms.

This module optimizes over a finite library of posted-price branches. Each
branch fixes participant charges before WTP reports arrive, then uses a monotone
quota rule over accept/reject decisions. A lottery over these branches preserves
truthfulness because reports never affect the branch's prices.

Install the optional dependencies before solving:

    uv sync --extra laboratory
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations, product
from math import ceil
from typing import Iterable, Sequence


ValuationProfile = tuple[float, ...]


@dataclass(frozen=True)
class PostedPriceBranch:
    """A deterministic truthful branch in the randomized mechanism library.

    ``charges`` are paid by all participants if the branch funds the chore.
    ``acceptance_weights`` and ``quota`` define the monotone decision rule:

        fund iff sum_i weight_i * 1[b_i >= charge_i] >= quota

    The branch is DSIC: if an agent is pivotal, accepting is better exactly when
    their true value covers their fixed charge. If not pivotal, their report does
    not change the branch outcome or payment.
    """

    name: str
    charges: tuple[float, ...]
    acceptance_weights: tuple[float, ...]
    quota: float

    def __post_init__(self) -> None:
        if len(self.charges) != len(self.acceptance_weights):
            raise ValueError("charges and acceptance_weights must have the same length")
        if any(charge < 0 for charge in self.charges):
            raise ValueError("charges must be nonnegative")
        if any(weight < 0 for weight in self.acceptance_weights):
            raise ValueError("acceptance weights must be nonnegative")
        if self.quota <= 0:
            raise ValueError("quota must be positive")

    def funds(self, reports: Sequence[float]) -> bool:
        if len(reports) != len(self.charges):
            raise ValueError("report vector has the wrong length")
        support = sum(
            weight
            for report, charge, weight in zip(reports, self.charges, self.acceptance_weights)
            if report >= charge
        )
        return support + 1e-9 >= self.quota

    def welfare(self, values: Sequence[float], cost: float) -> float:
        return (sum(values) - cost) if self.funds(values) else 0.0

    def expected_payments_if_chosen(self, values: Sequence[float]) -> tuple[float, ...]:
        return self.charges if self.funds(values) else tuple(0.0 for _ in self.charges)


@dataclass(frozen=True)
class OptimizedMixture:
    """Solution to an LP over posted-price branches."""

    branches: tuple[PostedPriceBranch, ...]
    weights: tuple[float, ...]
    objective_value: float
    objective_name: str

    def nonzero_weights(self, tolerance: float = 1e-8) -> list[tuple[PostedPriceBranch, float]]:
        return [
            (branch, weight)
            for branch, weight in zip(self.branches, self.weights)
            if weight > tolerance
        ]

    def chore_probability(self, values: Sequence[float]) -> float:
        return sum(weight for branch, weight in zip(self.branches, self.weights) if branch.funds(values))

    def expected_welfare(self, values: Sequence[float], cost: float) -> float:
        return sum(
            weight * branch.welfare(values, cost)
            for branch, weight in zip(self.branches, self.weights)
        )

    def expected_payments(self, values: Sequence[float]) -> tuple[float, ...]:
        if not self.branches:
            return ()
        totals = [0.0 for _ in self.branches[0].charges]
        for branch, weight in zip(self.branches, self.weights):
            payments = branch.expected_payments_if_chosen(values)
            for i, payment in enumerate(payments):
                totals[i] += weight * payment
        return tuple(totals)


def efficient_welfare(values: Sequence[float], cost: float) -> float:
    """First-best public-project welfare for one chore."""

    return max(0.0, sum(values) - cost)


def valuation_grid(n: int, levels: Sequence[float]) -> list[ValuationProfile]:
    """Return all n-agent profiles from a finite set of valuation levels."""

    return [tuple(profile) for profile in product(levels, repeat=n)]


def uniform_profile_weights(profiles: Sequence[ValuationProfile]) -> dict[ValuationProfile, float]:
    if not profiles:
        raise ValueError("profiles cannot be empty")
    weight = 1.0 / len(profiles)
    return {profile: weight for profile in profiles}


def build_default_branch_library(n: int, cost: float) -> list[PostedPriceBranch]:
    """Build a small, interpretable branch library.

    The library mixes democratic-style quota branches with sponsor-like branches:

    - equal split unanimity
    - equal split simple majority
    - weighted 2/3 supermajority
    - k-sponsor unanimity for every sponsor subset

    All branches are exactly budget-balanced when they fund the chore.
    """

    if n <= 0:
        raise ValueError("n must be positive")
    if cost <= 0:
        raise ValueError("cost must be positive")

    branches: list[PostedPriceBranch] = []
    equal_charges = tuple(cost / n for _ in range(n))
    unit_weights = tuple(1.0 for _ in range(n))

    branches.append(
        PostedPriceBranch(
            name="equal_unanimity",
            charges=equal_charges,
            acceptance_weights=unit_weights,
            quota=float(n),
        )
    )
    branches.append(
        PostedPriceBranch(
            name="equal_majority",
            charges=equal_charges,
            acceptance_weights=unit_weights,
            quota=float(ceil((n + 1) / 2)),
        )
    )
    branches.append(
        PostedPriceBranch(
            name="equal_two_thirds",
            charges=equal_charges,
            acceptance_weights=unit_weights,
            quota=float(ceil(2 * n / 3)),
        )
    )

    participant_ids = range(n)
    for sponsor_count in range(1, n + 1):
        for sponsor_tuple in combinations(participant_ids, sponsor_count):
            sponsors = set(sponsor_tuple)
            charge = cost / sponsor_count
            charges = tuple(charge if i in sponsors else 0.0 for i in participant_ids)
            weights = tuple(1.0 if i in sponsors else 0.0 for i in participant_ids)
            label = ",".join(str(i) for i in sponsor_tuple)
            branches.append(
                PostedPriceBranch(
                    name=f"{sponsor_count}_sponsor_unanimity[{label}]",
                    charges=charges,
                    acceptance_weights=weights,
                    quota=float(sponsor_count),
                )
            )

    return branches


def _profile_weights(
    profiles: Sequence[ValuationProfile],
    weights: dict[ValuationProfile, float] | None,
) -> dict[ValuationProfile, float]:
    if weights is None:
        return uniform_profile_weights(profiles)
    missing = [profile for profile in profiles if profile not in weights]
    if missing:
        raise ValueError(f"missing weights for {len(missing)} profiles")
    total = sum(weights[profile] for profile in profiles)
    if total <= 0:
        raise ValueError("profile weights must have positive total mass")
    return {profile: weights[profile] / total for profile in profiles}


def _import_pyomo():
    try:
        import pyomo.environ as pyo
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Pyomo is required for LP solving. Install it with: uv sync --extra laboratory"
        ) from exc
    return pyo


def _solve_model(model, solver_name: str):
    pyo = _import_pyomo()
    solver = pyo.SolverFactory(solver_name)
    if not solver.available(exception_flag=False):
        raise RuntimeError(
            f"Pyomo solver {solver_name!r} is not available. "
            "Install the laboratory extra or pass a different solver."
        )
    result = solver.solve(model)
    termination = result.solver.termination_condition
    if termination != pyo.TerminationCondition.optimal:
        raise RuntimeError(f"LP did not solve to optimality: {termination}")


def solve_average_welfare(
    branches: Sequence[PostedPriceBranch],
    profiles: Sequence[ValuationProfile],
    cost: float,
    profile_weights: dict[ValuationProfile, float] | None = None,
    solver_name: str = "appsi_highs",
) -> OptimizedMixture:
    """Maximize average welfare over the valuation grid.

    Because this objective is linear and there are no fairness/robustness
    constraints, the solution will often put all mass on one branch. Use
    ``solve_minimax_regret`` when you want a genuinely mixed robust mechanism.
    """

    if not branches:
        raise ValueError("branches cannot be empty")
    if not profiles:
        raise ValueError("profiles cannot be empty")

    pyo = _import_pyomo()
    weights_by_profile = _profile_weights(profiles, profile_weights)
    branch_scores = [
        sum(
            weights_by_profile[profile] * branch.welfare(profile, cost)
            for profile in profiles
        )
        for branch in branches
    ]

    model = pyo.ConcreteModel()
    model.M = pyo.RangeSet(0, len(branches) - 1)
    model.lam = pyo.Var(model.M, domain=pyo.NonNegativeReals)
    model.simplex = pyo.Constraint(expr=sum(model.lam[m] for m in model.M) == 1)
    model.objective = pyo.Objective(
        expr=sum(branch_scores[m] * model.lam[m] for m in model.M),
        sense=pyo.maximize,
    )

    _solve_model(model, solver_name)

    weights = tuple(float(pyo.value(model.lam[m])) for m in model.M)
    return OptimizedMixture(
        branches=tuple(branches),
        weights=weights,
        objective_value=float(pyo.value(model.objective)),
        objective_name="average_welfare",
    )


def solve_minimax_regret(
    branches: Sequence[PostedPriceBranch],
    profiles: Sequence[ValuationProfile],
    cost: float,
    solver_name: str = "appsi_highs",
) -> OptimizedMixture:
    """Minimize worst-case regret against first-best efficient welfare."""

    if not branches:
        raise ValueError("branches cannot be empty")
    if not profiles:
        raise ValueError("profiles cannot be empty")

    pyo = _import_pyomo()
    welfare = {
        (m, k): branches[m].welfare(profile, cost)
        for m in range(len(branches))
        for k, profile in enumerate(profiles)
    }
    optimal = [efficient_welfare(profile, cost) for profile in profiles]

    model = pyo.ConcreteModel()
    model.M = pyo.RangeSet(0, len(branches) - 1)
    model.K = pyo.RangeSet(0, len(profiles) - 1)
    model.lam = pyo.Var(model.M, domain=pyo.NonNegativeReals)
    model.regret = pyo.Var(domain=pyo.NonNegativeReals)
    model.simplex = pyo.Constraint(expr=sum(model.lam[m] for m in model.M) == 1)

    def regret_bound(model, k):
        mechanism_welfare = sum(welfare[(m, k)] * model.lam[m] for m in model.M)
        return model.regret >= optimal[k] - mechanism_welfare

    model.regret_bounds = pyo.Constraint(model.K, rule=regret_bound)
    model.objective = pyo.Objective(expr=model.regret, sense=pyo.minimize)

    _solve_model(model, solver_name)

    weights = tuple(float(pyo.value(model.lam[m])) for m in model.M)
    return OptimizedMixture(
        branches=tuple(branches),
        weights=weights,
        objective_value=float(pyo.value(model.objective)),
        objective_name="minimax_regret",
    )


def summarize_solution(
    mixture: OptimizedMixture,
    profiles: Iterable[ValuationProfile],
    cost: float,
    tolerance: float = 1e-8,
) -> str:
    """Return a compact human-readable solution report."""

    lines = [
        f"objective: {mixture.objective_name}",
        f"value: {mixture.objective_value:.6g}",
        "nonzero branch weights:",
    ]
    for branch, weight in mixture.nonzero_weights(tolerance):
        charges = ", ".join(f"{charge:.2f}" for charge in branch.charges)
        lines.append(f"  {weight:.4f}  {branch.name}  charges=({charges}) quota={branch.quota:g}")

    worst_regret = 0.0
    worst_profile: ValuationProfile | None = None
    for profile in profiles:
        regret = efficient_welfare(profile, cost) - mixture.expected_welfare(profile, cost)
        if regret > worst_regret + 1e-9:
            worst_regret = regret
            worst_profile = profile

    if worst_profile is not None:
        lines.append(f"worst regret on grid: {worst_regret:.6g} at {worst_profile}")
    return "\n".join(lines)

