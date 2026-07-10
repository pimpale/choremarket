import pytest

from laboratory.domain import Type, efficient_outcome, welfare
from laboratory.integrated import EqualSplitFirstBest


def test_first_best_uses_total_wtp_and_lowest_effort_cost():
    profile = (Type(0, 1), Type(1, 1), Type(1, 0))
    assert efficient_outcome(profile) == 2
    assert welfare(profile, 2) == 2
    assert efficient_outcome((Type(0, 1), Type(0, 1), Type(0, 1))) is None


def test_equal_split_first_best_finances_cost_without_changing_welfare():
    profile = (Type(4, 9), Type(3, 3), Type(2, 6))
    result = EqualSplitFirstBest().run(profile)

    assert result.probabilities == {1: 1.0}
    assert result.expected_transfers() == pytest.approx((-1, 2, -1))
    assert sum(result.expected_transfers()) == pytest.approx(0)
    assert result.expected_welfare(profile) == sum(t.value for t in profile) - profile[1].cost


def test_equal_split_first_best_skips_and_moves_no_money_when_value_is_too_low():
    result = EqualSplitFirstBest().run((Type(0, 3), Type(1, 4), Type(0, 5)))
    assert result.probabilities == {None: 1.0}
    assert result.expected_transfers() == pytest.approx((0, 0, 0))
