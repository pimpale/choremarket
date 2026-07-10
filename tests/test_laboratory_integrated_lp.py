import pytest

from laboratory.audit import audit_mechanism
from laboratory.domain import ChoreDomain, Type
from laboratory.integrated_lp import solve_integrated_lp


pytest.importorskip("pyomo.environ")


@pytest.fixture(scope="module")
def domain():
    return ChoreDomain.rectangular(3, [0, 1], [0, 1])


@pytest.fixture(scope="module")
def solution(domain):
    return solve_integrated_lp(domain)


def test_full_lp_enforces_joint_dsic_exact_bb_and_zero_no_chore_transfers(domain, solution):
    audit = audit_mechanism(solution.mechanism, domain)
    assert audit.max_joint_deviation_gain <= 2e-6
    assert audit.max_expected_budget_error <= 2e-6
    assert audit.max_conditional_budget_error <= 2e-6
    for lottery in solution.mechanism.table.values():
        assert lottery.transfer_mass.get(None, (0, 0, 0)) == pytest.approx(
            (0, 0, 0), abs=2e-6
        )


def test_ex_ante_ir_is_audited_but_not_added_as_a_redundant_constraint(domain, solution):
    audit = audit_mechanism(solution.mechanism, domain)
    assert audit.min_ex_ante_utility >= -2e-6
    assert solution.ex_ante_utility_by_agent == pytest.approx(
        audit.ex_ante_utility_by_agent, abs=2e-6
    )
    assert max(solution.ex_ante_utility_by_agent) == pytest.approx(
        min(solution.ex_ante_utility_by_agent), abs=2e-6
    )


def test_n4_reduced_grid_is_tractable_smoke():
    domain = ChoreDomain(n=4, types=(Type(0, 0), Type(1, 1)))
    solution = solve_integrated_lp(domain)
    assert len(solution.mechanism.table) == 2**4
    assert audit_mechanism(solution.mechanism, domain).max_joint_deviation_gain <= 2e-6
