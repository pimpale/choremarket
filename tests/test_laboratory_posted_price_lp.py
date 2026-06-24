import pytest

from laboratory.posted_price_lp import (
    build_default_branch_library,
    solve_minimax_regret,
    valuation_grid,
)


def test_default_branch_library_is_budget_balanced_when_funded():
    cost = 120.0
    branches = build_default_branch_library(n=4, cost=cost)

    assert branches
    for branch in branches:
        assert sum(branch.charges) == pytest.approx(cost)


def test_posted_price_branches_are_monotone_in_reports():
    branch = build_default_branch_library(n=3, cost=90.0)[1]  # equal majority

    low_report = [29.0, 30.0, 29.0]
    high_report = [30.0, 30.0, 29.0]

    assert branch.funds(low_report) is False
    assert branch.funds(high_report) is True


def test_minimax_regret_lp_smoke():
    pyo = pytest.importorskip("pyomo.environ")
    if not pyo.SolverFactory("appsi_highs").available(exception_flag=False):
        pytest.skip("appsi_highs solver is not available")

    cost = 100.0
    profiles = valuation_grid(n=3, levels=[0.0, 50.0, 100.0])
    branches = build_default_branch_library(n=3, cost=cost)

    solution = solve_minimax_regret(branches, profiles, cost)

    assert sum(solution.weights) == pytest.approx(1.0)
    assert solution.objective_value >= 0.0
    assert solution.nonzero_weights()
