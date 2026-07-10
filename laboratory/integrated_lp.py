"""Full finite-domain randomized mechanism LP.

The LP jointly chooses allocation probabilities and outcome-contingent transfer
mass. Incentive constraints cover every joint (value, cost) misreport. Budget
balance is imposed separately for every profile and realized outcome.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import permutations
from .domain import ChoreDomain, Outcome, Profile, gross_utility, replace_type, welfare
from .mechanism import Lottery, TabularMechanism
from .solver import pyomo, solve


@dataclass(frozen=True)
class IntegratedLPSolution:
    mechanism: TabularMechanism
    worst_case_regret: float
    average_welfare: float
    ex_ante_utility_by_agent: tuple[float, ...]
    transfers_regularized: bool
    transfer_bound: float
    max_conditional_transfer: float

    @property
    def transfer_bound_is_active(self) -> bool:
        return self.max_conditional_transfer >= self.transfer_bound - 1e-5


def solve_integrated_lp(
    domain: ChoreDomain,
    solver_name: str = "appsi_highs",
    transfer_bound: float | None = None,
    enforce_anonymity: bool = True,
    regularize_transfers: bool | None = None,
    regret_tolerance: float = 1e-7,
) -> IntegratedLPSolution:
    """Solve the lexicographic minimax-regret/average-welfare design LP."""

    pyo = pyomo()
    profiles = tuple(domain.profiles())
    profile_index = {profile: p for p, profile in enumerate(profiles)}
    outcomes = domain.outcomes
    regularize = domain.profile_count <= 1000 if regularize_transfers is None else regularize_transfers
    # The bound makes z=0 whenever x=0. It is deliberately generous relative
    # to every utility difference on the domain and reported in the solution so
    # experiments can verify it is slack or rerun with a larger value.
    scale = max(max(t.value, t.cost) for t in domain.types)
    bound = transfer_bound if transfer_bound is not None else max(1.0, 4 * domain.n * scale)
    if bound <= 0:
        raise ValueError("transfer_bound must be positive")

    model = pyo.ConcreteModel()
    model.P = pyo.RangeSet(0, len(profiles) - 1)
    model.O = pyo.RangeSet(0, len(outcomes) - 1)
    model.I = pyo.RangeSet(0, domain.n - 1)
    model.x = pyo.Var(model.P, model.O, domain=pyo.NonNegativeReals, bounds=(0.0, 1.0))
    model.z = pyo.Var(model.P, model.O, model.I, domain=pyo.Reals)
    model.regret = pyo.Var(domain=pyo.NonNegativeReals)

    model.simplex = pyo.Constraint(
        model.P,
        rule=lambda m, p: sum(m.x[p, o] for o in m.O) == 1,
    )
    model.balance = pyo.Constraint(
        model.P,
        model.O,
        rule=lambda m, p, o: sum(m.z[p, o, i] for i in m.I) == 0,
    )
    model.transfer_upper = pyo.Constraint(
        model.P,
        model.O,
        model.I,
        rule=lambda m, p, o, i: m.z[p, o, i] <= bound * m.x[p, o],
    )
    model.transfer_lower = pyo.Constraint(
        model.P,
        model.O,
        model.I,
        rule=lambda m, p, o, i: m.z[p, o, i] >= -bound * m.x[p, o],
    )
    model.no_chore_transfers = pyo.Constraint(
        model.P,
        model.I,
        rule=lambda m, p, i: m.z[p, 0, i] == 0,
    )

    def expected_utility_expr(m, report_p: int, agent: int, true_type):
        return sum(
            m.x[report_p, o] * gross_utility(agent, true_type, outcomes[o])
            + m.z[report_p, o, agent]
            for o in m.O
        )

    deviation_keys = []
    deviation_report_index: dict[tuple[int, int, int], int] = {}
    for p, profile in enumerate(profiles):
        for i in range(domain.n):
            for r, alternate in enumerate(domain.types):
                if alternate == profile[i]:
                    continue
                key = (p, i, r)
                deviation_keys.append(key)
                deviation_report_index[key] = profile_index[replace_type(profile, i, alternate)]
    model.D = pyo.Set(initialize=deviation_keys, dimen=3)

    def dsic_rule(m, p, i, r):
        true_type = profiles[p][i]
        deviating_p = deviation_report_index[(p, i, r)]
        return expected_utility_expr(m, p, i, true_type) >= expected_utility_expr(
            m, deviating_p, i, true_type
        )

    model.dsic = pyo.Constraint(model.D, rule=dsic_rule)

    optimal_welfare = [max(welfare(profile, outcome) for outcome in outcomes) for profile in profiles]

    def achieved_welfare(m, p):
        return sum(m.x[p, o] * welfare(profiles[p], outcomes[o]) for o in m.O)

    model.regret_bounds = pyo.Constraint(
        model.P,
        rule=lambda m, p: m.regret >= optimal_welfare[p] - achieved_welfare(m, p),
    )

    average_welfare_expr = sum(achieved_welfare(model, p) for p in model.P) / len(profiles)
    model.primary_objective = pyo.Objective(expr=model.regret, sense=pyo.minimize)
    solve(model, solver_name)
    best_regret = float(pyo.value(model.regret))

    model.primary_objective.deactivate()
    model.regret_tie = pyo.Constraint(expr=model.regret <= best_regret + regret_tolerance)
    model.secondary_objective = pyo.Objective(expr=average_welfare_expr, sense=pyo.maximize)
    solve(model, solver_name)

    if regularize:
        # IC/BB payment rules are often non-unique. Select a numerically tame
        # rule on small grids without changing either welfare objective. This
        # pass doubles the variable count, so fine grids skip it by default.
        best_average = float(pyo.value(average_welfare_expr))
        model.secondary_objective.deactivate()
        model.average_tie = pyo.Constraint(
            expr=average_welfare_expr >= best_average - regret_tolerance
        )
        model.abs_z = pyo.Var(model.P, model.O, model.I, domain=pyo.NonNegativeReals)
        model.abs_z_positive = pyo.Constraint(
            model.P,
            model.O,
            model.I,
            rule=lambda m, p, o, i: m.abs_z[p, o, i] >= m.z[p, o, i],
        )
        model.abs_z_negative = pyo.Constraint(
            model.P,
            model.O,
            model.I,
            rule=lambda m, p, o, i: m.abs_z[p, o, i] >= -m.z[p, o, i],
        )
        model.tertiary_objective = pyo.Objective(
            expr=sum(
                model.abs_z[p, o, i] for p in model.P for o in model.O for i in model.I
            ),
            sense=pyo.minimize,
        )
        solve(model, solver_name)

    table: dict[Profile, Lottery] = {}
    for p, profile in enumerate(profiles):
        raw_probabilities: dict[Outcome, float] = {}
        raw_masses: dict[Outcome, tuple[float, ...]] = {}
        for o, outcome in enumerate(outcomes):
            probability = max(0.0, float(pyo.value(model.x[p, o])))
            # HiGHS may return ~1e-9 probability with much larger conditional
            # transfers at a degenerate vertex. Such branches are below the LP
            # solve tolerance; discard them and renormalize the realized lottery.
            if probability <= regret_tolerance:
                continue
            transfers = [float(pyo.value(model.z[p, o, i])) for i in range(domain.n)]
            # Remove the residual floating-point budget error exactly.
            transfers[-1] -= sum(transfers)
            raw_probabilities[outcome] = probability
            raw_masses[outcome] = tuple(transfers)

        total_probability = sum(raw_probabilities.values())
        probabilities = {
            outcome: probability / total_probability
            for outcome, probability in raw_probabilities.items()
        }
        masses = {
            outcome: tuple(transfer / total_probability for transfer in raw_masses[outcome])
            for outcome in raw_probabilities
        }
        table[profile] = Lottery(probabilities=probabilities, transfer_mass=masses)

    if enforce_anonymity:
        # The feasible set and both welfare objectives are symmetric and convex.
        # Averaging all relabelings therefore preserves DSIC/BB/objectives while
        # avoiding hundreds of thousands of explicit permutation equalities.
        relabelings = tuple(permutations(range(domain.n)))
        anonymous_table: dict[Profile, Lottery] = {}
        for profile in profiles:
            probability_totals: dict[Outcome, float] = {}
            mass_totals: dict[Outcome, list[float]] = {}
            for relabeling in relabelings:
                permuted_profile = tuple(profile[old] for old in relabeling)
                lottery = table[permuted_profile]
                for permuted_outcome, probability in lottery.probabilities.items():
                    outcome = (
                        None if permuted_outcome is None else relabeling[permuted_outcome]
                    )
                    probability_totals[outcome] = probability_totals.get(outcome, 0.0) + (
                        probability / len(relabelings)
                    )
                    mass = mass_totals.setdefault(outcome, [0.0] * domain.n)
                    permuted_mass = lottery.transfer_mass[permuted_outcome]
                    for new_position, old_agent in enumerate(relabeling):
                        mass[old_agent] += permuted_mass[new_position] / len(relabelings)
            anonymous_table[profile] = Lottery(
                probabilities=probability_totals,
                transfer_mass={outcome: tuple(mass) for outcome, mass in mass_totals.items()},
            )
        table = anonymous_table

    max_conditional = 0.0
    for lottery in table.values():
        for outcome, probability in lottery.probabilities.items():
            max_conditional = max(
                max_conditional,
                *(
                    abs(transfer / probability)
                    for transfer in lottery.transfer_mass[outcome]
                ),
            )

    ex_ante_utility = tuple(
        sum(table[profile].expected_utility(i, profile[i]) for profile in profiles)
        / len(profiles)
        for i in range(domain.n)
    )
    return IntegratedLPSolution(
        mechanism=TabularMechanism("lp_integrated_supply_demand", domain, table),
        worst_case_regret=float(pyo.value(model.regret)),
        average_welfare=float(pyo.value(average_welfare_expr)),
        ex_ante_utility_by_agent=ex_ante_utility,
        transfers_regularized=regularize,
        transfer_bound=bound,
        max_conditional_transfer=max_conditional,
    )
