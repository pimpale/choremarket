from itertools import product

import pytest

from laboratory.audit import audit_mechanism
from laboratory.demand_lp import audit_demand_solution
from laboratory.domain import ChoreDomain, Type
from laboratory.evaluation import profile_results
from laboratory.sequential import (
    EqualShareMajoritySequential,
    EqualSplitVickreyFaltingsFair,
    GuoAsymptoticSequential,
    GuoFiniteNSequential,
    UnrestrictedDemandSequential,
    guo_redistribution_h,
    procurement,
)


DOMAIN = ChoreDomain.rectangular(3, [0, 1], [0, 1])


def test_reverse_vickrey_selects_lowest_bid_and_second_price():
    performer, winning, price = procurement((Type(1, 2), Type(1, 0), Type(1, 1)))
    assert (performer, winning, price) == (1, 0, 1)


def test_majority_uses_equal_share_acceptance_and_balances():
    mechanism = EqualShareMajoritySequential()
    funded = mechanism.run((Type(0, 2), Type(1, 0), Type(1, 3)))
    assert funded.probabilities == {1: 1.0}  # price=2, share=2/3, two supporters
    assert sum(funded.expected_transfers()) == pytest.approx(0)
    skipped = mechanism.run((Type(0, 2), Type(0, 0), Type(1, 3)))
    assert skipped.probabilities == {None: 1.0}


def test_faltings_fair_excludes_demand_agents_not_supply_bidders():
    # Performer 0, Vickrey price 3, equal share 1. Net demand values are
    # (1,-1,-1): excluding agent 0 skips; either other exclusion funds.
    result = EqualSplitVickreyFaltingsFair().run(
        (Type(2, 0), Type(0, 3), Type(0, 3))
    )
    assert result.probabilities == pytest.approx({None: 1 / 3, 0: 2 / 3})
    assert sum(result.expected_transfers()) == pytest.approx(0, abs=1e-9)
    for outcome, mass in result.transfer_mass.items():
        assert sum(mass) == pytest.approx(0, abs=1e-9)


@pytest.mark.parametrize(
    "mechanism_type", (GuoAsymptoticSequential, GuoFiniteNSequential)
)
def test_guo_uses_the_second_price_as_the_public_project_cost(mechanism_type):
    mechanism = mechanism_type()

    # Agent 1 wins at a second price of 3. Total WTP below/above that exact
    # price drives Guo's efficient fixed-price public-project decision.
    skipped = mechanism.run((Type(1, 3), Type(1, 0), Type(0, 5)))
    funded = mechanism.run((Type(1, 3), Type(1, 0), Type(1, 5)))

    assert skipped.probabilities == {None: 1.0}
    assert funded.probabilities == {1: 1.0}
    assert sum(skipped.expected_transfers()) <= 1e-9
    assert sum(funded.expected_transfers()) <= 1e-9


@pytest.mark.parametrize("variant", ("asymptotic", "finite_n"))
def test_guo_h_is_independent_of_the_agents_own_value(variant):
    left = guo_redistribution_h((0.0, 1.0, 2.0, 3.0), 2.5, variant)
    right = guo_redistribution_h((9.0, 1.0, 2.0, 3.0), 2.5, variant)
    assert left[0] == pytest.approx(right[0])


def test_guo_finite_n_implementation_satisfies_theorem_3_bounds():
    n = 4
    alpha = (n + 1) / (2 * n)
    for price in (0.0, 1.0, 3.0):
        for values in product((0.0, 0.5, 2.0), repeat=n):
            first_best = max(sum(values), price)
            h_sum = sum(guo_redistribution_h(values, price, "finite_n"))
            assert h_sum >= (n - 1) * first_best - 1e-9
            assert h_sum <= (n - alpha) * first_best + 1e-9


@pytest.mark.parametrize("variant", ("asymptotic", "finite_n"))
def test_guo_public_project_stage_is_truthful_at_a_fixed_price(variant):
    price = 1.5
    levels = (0.0, 1.0, 2.0)
    n = 3
    for true_values in product(levels, repeat=n):
        truthful_h = guo_redistribution_h(true_values, price, variant)
        truthful_funds = sum(true_values) >= price
        for i in range(n):
            if truthful_funds:
                truthful_transfer = (
                    sum(true_values) - true_values[i] - truthful_h[i] - price / n
                )
                truthful_utility = true_values[i] + truthful_transfer
            else:
                truthful_transfer = price * (n - 1) / n - truthful_h[i]
                truthful_utility = truthful_transfer

            for misreport in levels:
                reports = true_values[:i] + (misreport,) + true_values[i + 1 :]
                deviating_h = guo_redistribution_h(reports, price, variant)
                deviating_funds = sum(reports) >= price
                if deviating_funds:
                    deviating_transfer = (
                        sum(reports) - reports[i] - deviating_h[i] - price / n
                    )
                    deviating_utility = true_values[i] + deviating_transfer
                else:
                    deviating_transfer = price * (n - 1) / n - deviating_h[i]
                    deviating_utility = deviating_transfer
                assert deviating_utility <= truthful_utility + 1e-9


def test_guo_mechanisms_can_retain_surplus_but_never_need_a_subsidy():
    profiles = (
        (Type(0, 0), Type(1, 3), Type(1, 5)),
        (Type(0, 0), Type(0, 3), Type(4, 5)),
        (Type(4, 0), Type(4, 3), Type(4, 5)),
    )
    saw_strict_surplus = False
    for mechanism in (GuoAsymptoticSequential(), GuoFiniteNSequential()):
        for profile in profiles:
            balance = sum(mechanism.run(profile).expected_transfers())
            assert balance <= 1e-9
            saw_strict_surplus |= balance < -1e-9
    assert saw_strict_surplus


def test_unrestricted_demand_lp_is_wtp_dsic_and_recovers_the_fixed_price():
    mechanism = UnrestrictedDemandSequential(DOMAIN)
    for solution in mechanism.solutions.values():
        audit = audit_demand_solution(solution)
        assert audit["max_wtp_deviation_gain"] <= 2e-6
        assert audit["max_conditional_balance_error"] <= 2e-6


def test_composed_vickrey_demand_rules_are_audited_for_joint_deviations():
    mechanisms = (
        EqualShareMajoritySequential(),
        EqualSplitVickreyFaltingsFair(),
        UnrestrictedDemandSequential(DOMAIN),
    )
    for mechanism in mechanisms:
        audit = audit_mechanism(mechanism, DOMAIN)
        assert audit.max_expected_budget_error == pytest.approx(0, abs=1e-8)
        assert audit.max_joint_deviation_gain > 0

    majority = audit_mechanism(EqualShareMajoritySequential(), DOMAIN)
    assert majority.max_value_only_gain > 0
    assert majority.max_cost_only_gain > 0


def test_unrestricted_demand_lp_weakly_improves_faltings_worst_regret():
    faltings = EqualSplitVickreyFaltingsFair()
    demand_lp = UnrestrictedDemandSequential(DOMAIN)
    rows = profile_results([faltings, demand_lp], DOMAIN.profiles())
    worst = {
        mechanism: max(float(row["regret"]) for row in rows if row["mechanism"] == mechanism)
        for mechanism in (faltings.name, demand_lp.name)
    }
    assert worst[demand_lp.name] <= worst[faltings.name] + 2e-6
