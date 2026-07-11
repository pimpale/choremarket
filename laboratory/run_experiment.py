"""Solve, audit, compare, and plot the chore mechanisms."""

from __future__ import annotations

import argparse
from pathlib import Path

from .audit import audit_mechanism
from .demand_lp import audit_demand_solution
from .domain import ChoreDomain
from .evaluation import (
    plot_comparison,
    plot_regret_cdf,
    profile_results,
    summarize,
    write_rows,
    write_summaries,
)
from .integrated import EqualSplitFirstBest
from .integrated_lp import solve_integrated_lp
from .mechanism import QuantizedReports
from .profiling import TIMER, timed
from .sequential import (
    EqualSplitVickreyFaltingsFair,
    EqualShareMajoritySequential,
    RawBidDemandSequential,
)
from .solver import default_threads
from .synthetic import (
    generate_profiles,
    generate_uniform_profiles,
    quantize_profile,
    write_profiles_csv,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--participants", "-n", type=int, default=3)
    parser.add_argument("--values", type=float, nargs="+", default=(0, 15, 30, 45))
    parser.add_argument("--costs", type=float, nargs="+", default=(0, 15, 30, 45))
    parser.add_argument(
        "--demand-fine-values",
        type=float,
        nargs="+",
        default=(0, 7.5, 15, 22.5, 30, 37.5, 45),
        help="finer WTP grid for the second demand-LP variant",
    )
    parser.add_argument("--synthetic-count", type=int, default=500)
    parser.add_argument("--uniform-count", type=int, default=2000)
    parser.add_argument(
        "--demand-price-step",
        type=float,
        default=2.5,
        help="price-grid resolution for the raw-bid demand-LP variants",
    )
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
    parser.add_argument(
        "--audit",
        action="store_true",
        help="run the exhaustive IC/BB/IR and demand-LP audits (diagnostic only)",
    )
    args = parser.parse_args()

    TIMER.reset()
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

    fine_values = tuple(sorted(set(args.values) | set(args.demand_fine_values)))
    sorted_values = sorted(args.values)
    sorted_costs = sorted(args.costs)
    value_high = sorted_values[-1] + (sorted_values[-1] - sorted_values[-2]) / 2
    cost_high = sorted_costs[-1] + (sorted_costs[-1] - sorted_costs[-2]) / 2

    print("solving raw-bid demand LPs on the price grid", flush=True)
    step = args.demand_price_step
    price_levels = tuple(
        round(index * step, 6) for index in range(int(round(cost_high / step)) + 1)
    )
    raw_demand_coarse = RawBidDemandSequential(
        domain.n,
        args.values,
        price_levels,
        args.solver,
        name="lp_demand_vickrey_raw_coarse_wtp",
    )
    raw_demand_fine = RawBidDemandSequential(
        domain.n,
        fine_values,
        price_levels,
        args.solver,
        name="lp_demand_vickrey_raw_fine_wtp",
    )

    # Every comparison runs this one list; add a mechanism by appending an
    # entry. Each mechanism owns its deployment report interface: raw-bid and
    # formula mechanisms consume continuous types directly, tabular ones
    # quantize at the door via QuantizedReports.
    mechanisms = [
        EqualSplitFirstBest(),
        EqualShareMajoritySequential(),
        EqualSplitVickreyFaltingsFair(),
        raw_demand_coarse,
        raw_demand_fine,
        QuantizedReports(lp_solution.mechanism, args.values, args.costs),
    ]

    with timed("exhaustive evaluation"):
        profiles = tuple(domain.profiles())
        exhaustive_rows = profile_results(mechanisms, profiles)
    exhaustive_summaries = summarize(exhaustive_rows)
    write_rows(exhaustive_rows, output / "exhaustive_profiles.csv")
    write_summaries(exhaustive_summaries, output / "welfare_summary.csv")

    if args.audit:
        print("running exhaustive IC/BB/IR audits", flush=True)
        audit_rows = []
        for mechanism in mechanisms:
            with timed(f"exhaustive audit ({mechanism.name})"):
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

    demand_lp_rows = []
    for resolution, demand_lp in (
        ("coarse", raw_demand_coarse),
        ("fine", raw_demand_fine),
    ):
        for price, solution in demand_lp.solutions.items():
            row = {
                "resolution": resolution,
                "value_levels": repr(solution.value_levels),
                "vickrey_price": price,
                "worst_case_regret": solution.worst_case_regret,
                "average_welfare": solution.average_welfare,
                "transfer_bound": solution.transfer_bound,
                "max_conditional_transfer": solution.max_conditional_transfer,
            }
            if args.audit:
                with timed("demand LP audits"):
                    row.update(audit_demand_solution(solution))
            demand_lp_rows.append(row)
    write_rows(demand_lp_rows, output / "demand_lp_solutions.csv")

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
    write_profiles_csv(
        synthetic,
        output / "synthetic_bid_wtp.csv",
        args.values,
        args.costs,
        fine_values,
    )
    raw_profiles = [sample.types for sample in synthetic]
    coarse_profiles = [
        quantize_profile(sample.types, args.values, args.costs) for sample in synthetic
    ]
    fine_profiles = [
        quantize_profile(sample.types, fine_values, args.costs) for sample in synthetic
    ]
    with timed("synthetic evaluation"):
        synthetic_rows = profile_results(mechanisms, raw_profiles)

        rounding_rows = []
        for label, reports in (
            ("coarse_grid_oracle", coarse_profiles),
            ("fine_wtp_grid_oracle", fine_profiles),
        ):
            rounding_rows.extend(
                profile_results(
                    [EqualSplitFirstBest(name=label)],
                    reports,
                    true_profiles=raw_profiles,
                )
            )
    write_rows(synthetic_rows, output / "synthetic_welfare.csv")
    write_summaries(summarize(synthetic_rows), output / "synthetic_welfare_summary.csv")
    write_rows(rounding_rows, output / "synthetic_rounding_loss.csv")
    write_summaries(
        summarize(rounding_rows), output / "synthetic_rounding_loss_summary.csv"
    )
    with timed("figures"):
        plot_comparison(exhaustive_rows, synthetic_rows, output, rounding_rows)

    # Prior-free view of quantization loss: uniform true types capped at the
    # top rounding boundary, scored against the continuous first best. Every
    # mechanism handles its own report interface, so the raw draws go in
    # directly.
    uniform_raw = generate_uniform_profiles(
        domain.n, args.uniform_count, value_high, cost_high, args.seed + 1
    )
    with timed("uniform-prior evaluation"):
        uniform_rows = profile_results(mechanisms, uniform_raw)
    write_rows(uniform_rows, output / "uniform_prior_regret.csv")
    write_summaries(summarize(uniform_rows), output / "uniform_prior_regret_summary.csv")
    with timed("figures"):
        plot_regret_cdf(
            uniform_rows,
            output / "uniform_prior_regret_cdf.png",
            "Regret including quantization: uniform prior on "
            f"[0, {value_high:g}] values x [0, {cost_high:g}] costs",
        )

    write_rows(TIMER.rows(), output / "timings.csv")
    print("\n" + TIMER.report())

    print("\nExhaustive-grid summary")
    for summary in exhaustive_summaries:
        print(
            f"{summary.mechanism:38s} ratio={summary.welfare_ratio:7.3f} "
            f"avg_regret={summary.average_regret:8.3f} worst={summary.worst_case_regret:8.3f}"
        )
    print(f"\noutputs: {output.resolve()}")


if __name__ == "__main__":
    main()
