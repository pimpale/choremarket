"""Solve, audit, compare, and plot the chore mechanisms."""

from __future__ import annotations

import argparse
from pathlib import Path

from .audit import audit_mechanism
from .domain import ChoreDomain
from .evaluation import plot_comparison, profile_results, summarize, write_rows, write_summaries
from .integrated import EqualSplitFirstBest
from .integrated_lp import solve_integrated_lp
from .sequential import (
    EqualSplitVickreyFaltingsFair,
    EqualShareMajoritySequential,
    OptimizedDemandSequential,
)
from .solver import default_threads
from .synthetic import generate_profiles, quantize_profile, write_profiles_csv


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--participants", "-n", type=int, default=3)
    parser.add_argument("--values", type=float, nargs="+", default=(0, 15, 30))
    parser.add_argument("--costs", type=float, nargs="+", default=(0, 15, 30))
    parser.add_argument("--synthetic-count", type=int, default=500)
    parser.add_argument("--seed", type=int, default=20260710)
    parser.add_argument("--solver", default="appsi_highs")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument(
        "--cap-sensitivity",
        type=float,
        nargs="*",
        default=(),
        help="additional transfer caps for base-LP sensitivity solves",
    )
    parser.add_argument(
        "--no-anonymity",
        action="store_true",
        help="omit post-solve permutation averaging of the integrated LP",
    )
    args = parser.parse_args()

    domain = ChoreDomain.rectangular(args.participants, args.values, args.costs)
    output = args.output or Path("laboratory/results") / f"n{args.participants}"
    output.mkdir(parents=True, exist_ok=True)
    print(
        f"domain: n={domain.n}, types={len(domain.types)}, profiles={domain.profile_count}, "
        f"HiGHS threads={default_threads()}"
    )

    print("solving integrated supply/demand LP", flush=True)
    lp_solution = solve_integrated_lp(
        domain,
        solver_name=args.solver,
        enforce_anonymity=not args.no_anonymity,
    )

    mechanisms = [
        EqualSplitFirstBest(),
        EqualShareMajoritySequential(),
        EqualSplitVickreyFaltingsFair(),
        OptimizedDemandSequential(domain, args.solver),
        lp_solution.mechanism,
    ]

    profiles = tuple(domain.profiles())
    exhaustive_rows = profile_results(mechanisms, profiles)
    exhaustive_summaries = summarize(exhaustive_rows)
    write_rows(exhaustive_rows, output / "exhaustive_profiles.csv")
    write_summaries(exhaustive_summaries, output / "welfare_summary.csv")

    print("running exhaustive IC/BB/IR audits", flush=True)
    audit_rows = []
    for mechanism in mechanisms:
        report = audit_mechanism(mechanism, domain)
        audit_rows.append({"mechanism": mechanism.name, **report.__dict__})
    # Avoid serializing the nested diagnostic profile into the compact CSV.
    for row in audit_rows:
        row["worst_joint_deviation"] = repr(row["worst_joint_deviation"])
    write_rows(audit_rows, output / "mechanism_audits.csv")

    lp_rows = [{
        "worst_case_regret": lp_solution.worst_case_regret,
        "average_welfare": lp_solution.average_welfare,
        "ex_ante_utility_by_agent": repr(lp_solution.ex_ante_utility_by_agent),
        "transfers_regularized": lp_solution.transfers_regularized,
        "transfer_bound": lp_solution.transfer_bound,
        "max_conditional_transfer": lp_solution.max_conditional_transfer,
        "transfer_bound_is_active": lp_solution.transfer_bound_is_active,
    }]
    write_rows(lp_rows, output / "lp_solutions.csv")

    if args.cap_sensitivity:
        sensitivity_rows = []
        for bound in args.cap_sensitivity:
            print(f"base-LP transfer-cap sensitivity: {bound:g}", flush=True)
            solution = solve_integrated_lp(
                domain,
                solver_name=args.solver,
                transfer_bound=bound,
                enforce_anonymity=not args.no_anonymity,
            )
            sensitivity_rows.append(
                {
                    "transfer_bound": bound,
                    "worst_case_regret": solution.worst_case_regret,
                    "average_welfare": solution.average_welfare,
                    "max_conditional_transfer": solution.max_conditional_transfer,
                    "transfer_bound_is_active": solution.transfer_bound_is_active,
                }
            )
        write_rows(sensitivity_rows, output / "transfer_cap_sensitivity.csv")

    synthetic = generate_profiles(domain.n, args.synthetic_count, args.seed)
    write_profiles_csv(synthetic, output / "synthetic_bid_wtp.csv", args.values, args.costs)
    synthetic_profiles = [
        quantize_profile(sample.types, args.values, args.costs) for sample in synthetic
    ]
    synthetic_rows = profile_results(mechanisms, synthetic_profiles)
    write_rows(synthetic_rows, output / "synthetic_welfare.csv")
    write_summaries(summarize(synthetic_rows), output / "synthetic_welfare_summary.csv")
    plot_comparison(exhaustive_rows, synthetic_rows, output)

    print("\nExhaustive-grid summary")
    for summary in exhaustive_summaries:
        print(
            f"{summary.mechanism:38s} ratio={summary.welfare_ratio:7.3f} "
            f"avg_regret={summary.average_regret:8.3f} worst={summary.worst_case_regret:8.3f}"
        )
    print(f"\noutputs: {output.resolve()}")


if __name__ == "__main__":
    main()
