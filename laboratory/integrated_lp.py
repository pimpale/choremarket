"""Full finite-domain randomized mechanism LP.

The LP chooses allocation probabilities and each agent's expected transfer per
report profile. Incentives, regret, and welfare only depend on expected
transfers, so the per-outcome transfer mass is reconstructed after the solve
by spreading each expected transfer over the performing outcomes in proportion
to their probabilities. With the aggregated bound |t| <= bound * (1 - x_null)
this reconstruction satisfies per-outcome budget balance, the no-transfer null
outcome, and the conditional bound |z| <= bound * x exactly, and any feasible
per-outcome transfer plan projects onto a feasible expected-transfer plan, so
the reduction is lossless. Incentive constraints cover every joint
(value, cost) misreport; on large domains they are enforced lazily by
cutting rounds that add only the violated rows.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from itertools import combinations_with_replacement
from math import factorial

import numpy as np

from .domain import ChoreDomain, Outcome, Profile, Type, gross_utility, replace_type, welfare
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
    welfare_weight: float = 1e-4,
    ic_tolerance: float = 1e-6,
) -> IntegratedLPSolution:
    """Solve the scalarized minimax-regret/average-welfare design LP.

    The lexicographic (regret first, welfare second) objective is scalarized
    into ``regret - welfare_weight * average_welfare`` so one solve replaces
    two. The reported worst-case regret can exceed the true minimax value by
    at most ``welfare_weight`` times the average-welfare range of the domain.

    On domains too fine for the transfer-regularization pass, IC constraints
    are generated lazily: grid-adjacent misreports seed the model and cutting
    rounds add any misreport whose gain exceeds ``ic_tolerance``.
    """

    pyo = pyomo()
    all_profiles = tuple(domain.profiles())
    profiles = (
        tuple(combinations_with_replacement(domain.types, domain.n))
        if enforce_anonymity
        else all_profiles
    )
    profile_index = {profile: p for p, profile in enumerate(profiles)}
    outcomes = domain.outcomes
    regularize = (
        domain.profile_count <= 1000
        if regularize_transfers is None
        else regularize_transfers
    )
    # The bound makes z=0 whenever x=0. It is deliberately generous relative
    # to every utility difference on the domain and reported in the solution so
    # experiments can verify it is slack or rerun with a larger value.
    scale = max(max(t.value, t.cost) for t in domain.types)
    bound = transfer_bound if transfer_bound is not None else max(1.0, 4 * domain.n * scale)
    if bound <= 0:
        raise ValueError("transfer_bound must be positive")

    def canonicalize(profile: Profile) -> tuple[Profile, tuple[int, ...]]:
        """Return sorted profile and old-agent -> canonical-position map."""

        order = sorted(range(domain.n), key=lambda i: (profile[i], i))
        old_to_new = [0] * domain.n
        for new_position, old_agent in enumerate(order):
            old_to_new[old_agent] = new_position
        return tuple(profile[old_agent] for old_agent in order), tuple(old_to_new)

    def orbit_size(profile: Profile) -> int:
        size = factorial(domain.n)
        for count in Counter(profile).values():
            size //= factorial(count)
        return size

    profile_weights = (
        tuple(orbit_size(profile) / domain.profile_count for profile in profiles)
        if enforce_anonymity
        else tuple(1.0 / domain.profile_count for _ in profiles)
    )

    model = pyo.ConcreteModel()
    model.P = pyo.RangeSet(0, len(profiles) - 1)
    model.O = pyo.RangeSet(0, len(outcomes) - 1)
    model.I = pyo.RangeSet(0, domain.n - 1)
    model.x = pyo.Var(model.P, model.O, domain=pyo.NonNegativeReals, bounds=(0.0, 1.0))
    model.t = pyo.Var(model.P, model.I, domain=pyo.Reals)
    model.regret = pyo.Var(domain=pyo.NonNegativeReals)

    model.simplex = pyo.Constraint(
        model.P,
        rule=lambda m, p: sum(m.x[p, o] for o in m.O) == 1,
    )
    model.balance = pyo.Constraint(
        model.P,
        rule=lambda m, p: sum(m.t[p, i] for i in m.I) == 0,
    )
    # Aggregate of the per-outcome bound |z| <= bound * x over the performing
    # outcomes; outcome 0 is the null outcome, which carries no transfers.
    model.transfer_upper = pyo.Constraint(
        model.P,
        model.I,
        rule=lambda m, p, i: m.t[p, i] <= bound * (1 - m.x[p, 0]),
    )
    model.transfer_lower = pyo.Constraint(
        model.P,
        model.I,
        rule=lambda m, p, i: m.t[p, i] >= -bound * (1 - m.x[p, 0]),
    )

    def expected_utility_expr(m, report_p: int, agent: int, true_type):
        return (
            sum(
                m.x[report_p, o] * gross_utility(agent, true_type, outcomes[o])
                for o in m.O
            )
            + m.t[report_p, agent]
        )

    deviation_keys = []
    deviation_target: dict[tuple[int, int, int], tuple[int, int]] = {}
    for p, profile in enumerate(profiles):
        # With anonymity, agents sharing the same type and multiset of others
        # generate identical IC constraints; keep one representative.
        representative_agents = (
            [i for i in range(domain.n) if i == 0 or profile[i] != profile[i - 1]]
            if enforce_anonymity
            else range(domain.n)
        )
        for i in representative_agents:
            for r, alternate in enumerate(domain.types):
                if alternate == profile[i]:
                    continue
                key = (p, i, r)
                deviation_keys.append(key)
                deviated = replace_type(profile, i, alternate)
                if enforce_anonymity:
                    canonical, old_to_new = canonicalize(deviated)
                    deviation_target[key] = (profile_index[canonical], old_to_new[i])
                else:
                    deviation_target[key] = (profile_index[deviated], i)
    def dsic_rule(m, p, i, r):
        true_type = profiles[p][i]
        deviating_p, deviating_i = deviation_target[(p, i, r)]
        return expected_utility_expr(m, p, i, true_type) >= expected_utility_expr(
            m, deviating_p, deviating_i, true_type
        )

    # The regularization re-solves must keep every IC row in the model, so
    # lazy generation is only used on the large single-solve domains.
    lazy_ic = not regularize
    if lazy_ic:
        type_index = {t: k for k, t in enumerate(domain.types)}
        value_levels = sorted({t.value for t in domain.types})
        cost_levels = sorted({t.cost for t in domain.types})

        def is_adjacent(reported: Type, alternate: Type) -> bool:
            if reported.cost == alternate.cost:
                return (
                    abs(
                        value_levels.index(reported.value)
                        - value_levels.index(alternate.value)
                    )
                    == 1
                )
            if reported.value == alternate.value:
                return (
                    abs(
                        cost_levels.index(reported.cost)
                        - cost_levels.index(alternate.cost)
                    )
                    == 1
                )
            return False

        key_report = np.array([p for p, _, _ in deviation_keys])
        key_agent = np.array([i for _, i, _ in deviation_keys])
        key_type = np.array(
            [type_index[profiles[p][i]] for p, i, _ in deviation_keys]
        )
        targets = [deviation_target[key] for key in deviation_keys]
        key_dev_report = np.array([q for q, _ in targets])
        key_dev_agent = np.array([j for _, j in targets])
        gross = np.array(
            [
                [
                    [gross_utility(i, t, outcome) for outcome in outcomes]
                    for i in range(domain.n)
                ]
                for t in domain.types
            ]
        )
        enforced = np.array(
            [
                is_adjacent(profiles[p][i], domain.types[r])
                for p, i, r in deviation_keys
            ]
        )
        model.dsic = pyo.ConstraintList()
        for k in np.nonzero(enforced)[0]:
            model.dsic.add(dsic_rule(model, *deviation_keys[int(k)]))
    else:
        model.D = pyo.Set(initialize=deviation_keys, dimen=3)
        model.dsic = pyo.Constraint(model.D, rule=dsic_rule)

    if enforce_anonymity:
        # A canonical profile can contain identical types. Swapping two such
        # positions must swap their performer probabilities and transfers.
        stabilizer_keys = []
        for p, profile in enumerate(profiles):
            for left in range(domain.n - 1):
                if profile[left] == profile[left + 1]:
                    stabilizer_keys.append((p, left))
        model.S = pyo.Set(initialize=stabilizer_keys, dimen=2)

        def swapped(index: int, left: int) -> int:
            if index == left:
                return left + 1
            if index == left + 1:
                return left
            return index

        def stabilizer_allocation(m, p, left, o):
            outcome = outcomes[o]
            target = None if outcome is None else swapped(outcome, left)
            return m.x[p, o] == m.x[p, outcomes.index(target)]

        model.stabilizer_allocation = pyo.Constraint(
            model.S, model.O, rule=stabilizer_allocation
        )

        model.stabilizer_transfer = pyo.Constraint(
            model.S, rule=lambda m, p, left: m.t[p, left] == m.t[p, left + 1]
        )

    optimal_welfare = [
        max(welfare(profile, outcome) for outcome in outcomes) for profile in profiles
    ]

    def achieved_welfare(m, p):
        return sum(m.x[p, o] * welfare(profiles[p], outcomes[o]) for o in m.O)

    model.regret_bounds = pyo.Constraint(
        model.P,
        rule=lambda m, p: m.regret >= optimal_welfare[p] - achieved_welfare(m, p),
    )

    average_welfare_expr = sum(
        profile_weights[p] * achieved_welfare(model, p) for p in model.P
    )
    if welfare_weight <= 0:
        raise ValueError("welfare_weight must be positive")
    model.scalar_objective = pyo.Objective(
        expr=model.regret - welfare_weight * average_welfare_expr,
        sense=pyo.minimize,
    )
    # Interior point without crossover only when regularization is skipped;
    # the regularization re-solve pins the objectives to their optima, which
    # leaves the feasible set without an interior and defeats pure IPM.
    persistent_solver = solve(
        model, solver_name, method="ipm" if lazy_ic else None
    )

    if lazy_ic:
        for cutting_round in range(1, 26):
            x_values = np.array(
                [[pyo.value(model.x[p, o]) for o in model.O] for p in model.P]
            )
            t_values = np.array(
                [[pyo.value(model.t[p, i]) for i in model.I] for p in model.P]
            )
            gross_expected = np.einsum("po,tio->pti", x_values, gross)
            truthful = (
                gross_expected[key_report, key_type, key_agent]
                + t_values[key_report, key_agent]
            )
            deviating = (
                gross_expected[key_dev_report, key_type, key_dev_agent]
                + t_values[key_dev_report, key_dev_agent]
            )
            gains = deviating - truthful
            # Rows already in the model hold to solver tolerance; excluding
            # them keeps residual noise from re-triggering rounds.
            gains[enforced] = 0.0
            violated = np.nonzero(gains > ic_tolerance)[0]
            if violated.size == 0:
                break
            print(
                f"IC cutting round {cutting_round}: adding {violated.size} of "
                f"{len(deviation_keys)} rows (max gain {gains.max():.3g})",
                flush=True,
            )
            for k in violated:
                model.dsic.add(dsic_rule(model, *deviation_keys[int(k)]))
            enforced[violated] = True
            solve(model, solver_name, solver=persistent_solver)
        else:
            raise RuntimeError("IC constraint generation did not converge")

    if regularize:
        # IC/BB payment rules are often non-unique. Select a numerically tame
        # rule on small grids without changing either welfare objective. This
        # pass adds a variable per expected transfer, so fine grids skip it
        # by default.
        best_regret = float(pyo.value(model.regret))
        best_average = float(pyo.value(average_welfare_expr))
        model.scalar_objective.deactivate()
        model.regret_tie = pyo.Constraint(
            expr=model.regret <= best_regret + regret_tolerance
        )
        model.average_tie = pyo.Constraint(
            expr=average_welfare_expr >= best_average - regret_tolerance
        )
        model.abs_t = pyo.Var(model.P, model.I, domain=pyo.NonNegativeReals)
        model.abs_t_positive = pyo.Constraint(
            model.P,
            model.I,
            rule=lambda m, p, i: m.abs_t[p, i] >= m.t[p, i],
        )
        model.abs_t_negative = pyo.Constraint(
            model.P,
            model.I,
            rule=lambda m, p, i: m.abs_t[p, i] >= -m.t[p, i],
        )
        model.tertiary_objective = pyo.Objective(
            expr=sum(model.abs_t[p, i] for p in model.P for i in model.I),
            sense=pyo.minimize,
        )
        solve(model, solver_name, solver=persistent_solver)

    reduced_table: dict[Profile, Lottery] = {}
    for p, profile in enumerate(profiles):
        # Probability branches below the LP solve tolerance are numerical
        # noise; discard them and renormalize the realized lottery.
        kept = {
            o: probability
            for o in range(len(outcomes))
            if (probability := max(0.0, float(pyo.value(model.x[p, o])))) > 1e-10
        }
        performing_mass = sum(
            probability for o, probability in kept.items() if outcomes[o] is not None
        )
        expected_transfers = [
            float(pyo.value(model.t[p, i])) for i in range(domain.n)
        ]
        # Remove the residual floating-point budget error exactly.
        expected_transfers[-1] -= sum(expected_transfers)

        raw_probabilities: dict[Outcome, float] = {}
        raw_masses: dict[Outcome, tuple[float, ...]] = {}
        for o, probability in kept.items():
            outcome = outcomes[o]
            if outcome is None or performing_mass <= 1e-10:
                mass = (0.0,) * domain.n
            else:
                # Proportional disaggregation of the expected transfer; see
                # the module docstring.
                mass = tuple(
                    transfer * probability / performing_mass
                    for transfer in expected_transfers
                )
            raw_probabilities[outcome] = probability
            raw_masses[outcome] = mass

        total_probability = sum(raw_probabilities.values())
        probabilities = {
            outcome: probability / total_probability
            for outcome, probability in raw_probabilities.items()
        }
        masses = {
            outcome: tuple(
                transfer / total_probability for transfer in raw_masses[outcome]
            )
            for outcome in raw_probabilities
        }
        reduced_table[profile] = Lottery(probabilities=probabilities, transfer_mass=masses)

    if enforce_anonymity:
        table: dict[Profile, Lottery] = {}
        for profile in all_profiles:
            canonical, old_to_new = canonicalize(profile)
            new_to_old = [0] * domain.n
            for old_agent, new_position in enumerate(old_to_new):
                new_to_old[new_position] = old_agent
            reduced = reduced_table[canonical]
            probabilities: dict[Outcome, float] = {}
            masses: dict[Outcome, tuple[float, ...]] = {}
            for canonical_outcome, probability in reduced.probabilities.items():
                outcome = (
                    None if canonical_outcome is None else new_to_old[canonical_outcome]
                )
                probabilities[outcome] = probability
                canonical_mass = reduced.transfer_mass[canonical_outcome]
                mass = [0.0] * domain.n
                for old_agent, new_position in enumerate(old_to_new):
                    mass[old_agent] = canonical_mass[new_position]
                masses[outcome] = tuple(mass)
            table[profile] = Lottery(
                probabilities=probabilities,
                transfer_mass=masses,
            )
    else:
        table = reduced_table

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
        sum(table[profile].expected_utility(i, profile[i]) for profile in all_profiles)
        / len(all_profiles)
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
