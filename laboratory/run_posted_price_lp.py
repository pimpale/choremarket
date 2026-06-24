"""Run a small posted-price LP experiment.

Example:

    uv run --extra laboratory python -m laboratory.run_posted_price_lp
"""

from __future__ import annotations

import argparse

from laboratory.posted_price_lp import (
    build_default_branch_library,
    solve_average_welfare,
    solve_minimax_regret,
    summarize_solution,
    valuation_grid,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Optimize a posted-price chore mechanism.")
    parser.add_argument("--participants", "-n", type=int, default=3)
    parser.add_argument("--cost", "-c", type=float, default=100.0)
    parser.add_argument(
        "--levels",
        type=float,
        nargs="+",
        default=[0.0, 25.0, 50.0, 75.0, 100.0],
        help="finite valuation levels for each participant",
    )
    parser.add_argument(
        "--objective",
        choices=["minimax-regret", "average-welfare"],
        default="minimax-regret",
    )
    parser.add_argument("--solver", default="appsi_highs")
    args = parser.parse_args()

    profiles = valuation_grid(args.participants, args.levels)
    branches = build_default_branch_library(args.participants, args.cost)

    if args.objective == "average-welfare":
        mixture = solve_average_welfare(branches, profiles, args.cost, solver_name=args.solver)
    else:
        mixture = solve_minimax_regret(branches, profiles, args.cost, solver_name=args.solver)

    print(summarize_solution(mixture, profiles, args.cost))


if __name__ == "__main__":
    main()

