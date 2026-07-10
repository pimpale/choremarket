import pytest

from laboratory.audit import audit_mechanism
from laboratory.domain import ChoreDomain, Type
from laboratory.sequential import (
    EqualShareMajoritySequential,
    EqualSplitVickreyFaltingsFair,
    OptimizedDemandSequential,
    demand_branch_library,
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


def test_every_demand_branch_exactly_funds_the_vickrey_price():
    for branch in demand_branch_library(4, 12):
        assert sum(branch.charges) == pytest.approx(12)


def test_composed_vickrey_demand_rules_are_audited_for_joint_deviations():
    mechanisms = (
        EqualShareMajoritySequential(),
        EqualSplitVickreyFaltingsFair(),
        OptimizedDemandSequential(DOMAIN),
    )
    for mechanism in mechanisms:
        audit = audit_mechanism(mechanism, DOMAIN)
        assert audit.max_expected_budget_error == pytest.approx(0, abs=1e-8)
        assert audit.max_joint_deviation_gain > 0

    majority = audit_mechanism(EqualShareMajoritySequential(), DOMAIN)
    assert majority.max_value_only_gain > 0
    assert majority.max_cost_only_gain > 0
