"""Small Pyomo/HiGHS compatibility helpers."""

from __future__ import annotations

import os


def pyomo():
    try:
        import pyomo.environ as pyo
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Install the laboratory dependencies with: uv sync --extra laboratory"
        ) from exc
    return pyo


def default_threads() -> int:
    configured = os.environ.get("CHOREMARKET_LP_THREADS")
    if configured is not None:
        return max(1, int(configured))
    return min(8, os.cpu_count() or 1)


def make_solver(solver_name: str = "appsi_highs", threads: int | None = None):
    pyo = pyomo()
    solver = pyo.SolverFactory(solver_name)
    if not solver.available(exception_flag=False):
        raise RuntimeError(f"Pyomo solver {solver_name!r} is unavailable")
    if "highs" in solver_name.lower():
        solver.options["threads"] = threads or default_threads()
        solver.options["primal_feasibility_tolerance"] = 1e-9
        solver.options["dual_feasibility_tolerance"] = 1e-9
        solver.options["ipm_optimality_tolerance"] = 1e-10
        method = os.environ.get("CHOREMARKET_LP_METHOD")
        if method:
            solver.options["solver"] = method
    return solver


def solve(
    model,
    solver_name: str = "appsi_highs",
    threads: int | None = None,
    solver=None,
):
    pyo = pyomo()
    solver = solver or make_solver(solver_name, threads)
    result = solver.solve(model)
    if result.solver.termination_condition != pyo.TerminationCondition.optimal:
        raise RuntimeError(
            f"optimization did not terminate optimally: {result.solver.termination_condition}"
        )
    return solver
