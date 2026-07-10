"""Welfare summaries, CSV exports, and Matplotlib comparison figures."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from typing import Iterable, Sequence

from .domain import Profile, efficient_outcome, welfare
from .mechanism import Mechanism


DISPLAY_NAMES = {
    "equal_split_first_best": "EqualSplit + FirstBest",
    "equal_split_vickrey_majority": "EqualSplit + Vickrey + Majority",
    "equal_split_vickrey_faltings_fair": "EqualSplit + Vickrey + FaltingsFair",
    "lp_demand_vickrey": "LP Demand + Vickrey",
    "lp_demand_vickrey_coarse": "LP Demand + Vickrey (coarse WTP)",
    "lp_demand_vickrey_fine": "LP Demand + Vickrey (fine WTP)",
    "lp_integrated_supply_demand": "LP Integrated Supply/Demand",
    "coarse_grid_oracle": "Coarse grid oracle",
    "fine_wtp_grid_oracle": "Fine-WTP grid oracle",
}


def display_name(name: str) -> str:
    return DISPLAY_NAMES.get(name, name)


@dataclass(frozen=True)
class WelfareSummary:
    mechanism: str
    profiles: int
    average_welfare: float
    average_first_best: float
    welfare_ratio: float
    worst_case_regret: float
    average_regret: float
    average_abs_budget_imbalance: float


def profile_results(
    mechanisms: Sequence[Mechanism],
    profiles: Iterable[Profile],
    *,
    true_profiles: Iterable[Profile] | None = None,
) -> list[dict[str, float | int | str]]:
    """Evaluate mechanisms on reports, optionally scoring against raw types.

    When ``true_profiles`` is omitted, reports are also the true types, as in
    the exhaustive finite-domain comparison.  Supplying raw continuous types
    makes the synthetic comparison include report-grid rounding loss.
    """

    reported = tuple(profiles)
    true = reported if true_profiles is None else tuple(true_profiles)
    if len(reported) != len(true):
        raise ValueError(
            "reported and true profile collections must have equal length"
        )

    rows = []
    for profile_id, (report_profile, true_profile) in enumerate(zip(reported, true)):
        first_best = welfare(true_profile, efficient_outcome(true_profile))
        reported_first_best = welfare(true_profile, efficient_outcome(report_profile))
        for mechanism in mechanisms:
            result = mechanism.run(report_profile)
            achieved = result.expected_welfare(true_profile)
            rows.append(
                {
                    "profile_id": profile_id,
                    "mechanism": mechanism.name,
                    "first_best_welfare": first_best,
                    "reported_first_best_welfare": reported_first_best,
                    "welfare": achieved,
                    "regret": first_best - achieved,
                    "rounding_regret": first_best - reported_first_best,
                    "budget_imbalance": sum(result.expected_transfers()),
                }
            )
    return rows


def summarize(rows: Sequence[dict[str, float | int | str]]) -> list[WelfareSummary]:
    names = list(dict.fromkeys(str(row["mechanism"]) for row in rows))
    summaries = []
    for name in names:
        selected = [row for row in rows if row["mechanism"] == name]
        achieved = mean(float(row["welfare"]) for row in selected)
        benchmark = mean(float(row["first_best_welfare"]) for row in selected)
        regrets = [float(row["regret"]) for row in selected]
        summaries.append(
            WelfareSummary(
                mechanism=name,
                profiles=len(selected),
                average_welfare=achieved,
                average_first_best=benchmark,
                welfare_ratio=achieved / benchmark if benchmark else 1.0,
                worst_case_regret=max(regrets),
                average_regret=mean(regrets),
                average_abs_budget_imbalance=mean(
                    abs(float(row["budget_imbalance"])) for row in selected
                ),
            )
        )
    return summaries


def write_rows(rows: Sequence[dict], path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        return
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys(), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def write_summaries(summaries: Sequence[WelfareSummary], path: str | Path) -> None:
    write_rows([summary.__dict__ for summary in summaries], path)


def plot_comparison(
    exhaustive_rows: Sequence[dict],
    synthetic_rows: Sequence[dict],
    output_dir: str | Path,
    rounding_rows: Sequence[dict] = (),
) -> None:
    try:
        import matplotlib.pyplot as plt
    except ModuleNotFoundError as exc:
        raise RuntimeError("Matplotlib is required; run uv sync --extra laboratory") from exc

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    summaries = summarize(exhaustive_rows)
    names = [summary.mechanism for summary in summaries]
    ratios = [summary.welfare_ratio for summary in summaries]
    colors = ["#7f8c8d" if "vcg" in name or "first" in name else "#2878b5" for name in names]

    fig, ax = plt.subplots(figsize=(12, 6))
    ax.barh([display_name(name) for name in names], ratios, color=colors)
    ax.set_xlabel("Average welfare / first-best welfare")
    ax.set_xlim(left=min(0.0, min(ratios) - 0.05), right=max(1.02, max(ratios) + 0.05))
    ax.set_title("Exhaustive-grid welfare efficiency")
    ax.grid(axis="x", alpha=0.25)
    fig.tight_layout()
    fig.savefig(output_dir / "exhaustive_welfare_ratio.png", dpi=180)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(12, 6))
    for name in names:
        regrets = sorted(
            float(row["regret"])
            for row in exhaustive_rows
            if row["mechanism"] == name
        )
        y = [(i + 1) / len(regrets) for i in range(len(regrets))]
        ax.step(
            regrets,
            y,
            where="post",
            label=display_name(name),
            linewidth=1.5,
        )
    ax.set_xlabel("Welfare regret")
    ax.set_ylabel("Cumulative share of profiles")
    ax.set_title("Exhaustive-grid regret distribution")
    ax.grid(alpha=0.25)
    ax.legend(fontsize=7, ncol=2)
    fig.tight_layout()
    fig.savefig(output_dir / "exhaustive_regret_cdf.png", dpi=180)
    plt.close(fig)

    synthetic_names = list(dict.fromkeys(str(row["mechanism"]) for row in synthetic_rows))
    synthetic_summaries = summarize(synthetic_rows)
    synthetic_ratios = [summary.welfare_ratio for summary in synthetic_summaries]

    fig, ax = plt.subplots(figsize=(12, 6))
    positions = list(range(len(synthetic_names)))
    ax.barh(positions, synthetic_ratios, color="#5b8ff9")
    ax.set_yticks(positions, labels=[display_name(name) for name in synthetic_names])
    lower = max(0.0, min(synthetic_ratios) - 0.01)
    upper = max(1.001, max(synthetic_ratios) + 0.001)
    ax.set_xlim(lower, upper)
    ax.set_xlabel("Average raw welfare / continuous first-best welfare")
    ax.set_title("Synthetic efficiency, including report-grid rounding")
    ax.grid(axis="x", alpha=0.25)
    for position, ratio in enumerate(synthetic_ratios):
        ax.text(
            min(ratio + 0.0003, upper - 0.0002),
            position,
            f"{ratio:.4f}",
            va="center",
            fontsize=9,
        )
    fig.tight_layout()
    fig.savefig(output_dir / "synthetic_welfare_ratio.png", dpi=180)
    plt.close(fig)

    if rounding_rows:
        rounding_summaries = summarize(rounding_rows)
        rounding_names = [summary.mechanism for summary in rounding_summaries]
        rounding_ratios = [summary.welfare_ratio for summary in rounding_summaries]
        losses = [1.0 - ratio for ratio in rounding_ratios]

        fig, ax = plt.subplots(figsize=(10, 4.5))
        positions = list(range(len(rounding_names)))
        ax.barh(positions, losses, color="#d98c3f")
        ax.set_yticks(
            positions, labels=[display_name(name) for name in rounding_names]
        )
        ax.set_xlabel("Welfare loss relative to continuous first best")
        ax.set_title("Allocation loss caused by report-grid rounding")
        ax.set_xlim(0, max(0.01, max(losses) * 1.18))
        ax.grid(axis="x", alpha=0.25)
        for position, loss in enumerate(losses):
            ax.text(
                loss + max(losses) * 0.015,
                position,
                f"{loss:.2%}",
                va="center",
                fontsize=9,
            )
        fig.tight_layout()
        fig.savefig(output_dir / "synthetic_rounding_loss.png", dpi=180)
        plt.close(fig)

    data = [
        [float(row["regret"]) for row in synthetic_rows if row["mechanism"] == name]
        for name in synthetic_names
    ]
    average_regret = [mean(regrets) for regrets in data]
    nonzero_rate = [sum(regret > 1e-8 for regret in regrets) / len(regrets) for regrets in data]
    worst_regret = [max(regrets) for regrets in data]

    # A conventional boxplot is flat when at least 75% of quantized samples
    # have zero regret. Mean loss, nonzero frequency, and the worst tail remain
    # informative in exactly that case.
    fig, (left, right) = plt.subplots(1, 2, figsize=(14, 6))
    positions = list(range(len(synthetic_names)))
    left.barh(positions, average_regret, color="#2878b5")
    left.set_yticks(
        positions, labels=[display_name(name) for name in synthetic_names]
    )
    left.set_xlabel("Mean welfare regret")
    left.set_title("Average synthetic loss")
    left.grid(axis="x", alpha=0.25)

    right.barh(positions, nonzero_rate, color="#69a761")
    right.set_yticks(positions, labels=[])
    right.set_xlabel("Share of profiles with nonzero regret")
    right.set_xlim(0, max(1.0, max(nonzero_rate) * 1.12))
    right.set_title("Loss frequency and worst tail")
    right.grid(axis="x", alpha=0.25)
    for position, (rate, worst) in enumerate(zip(nonzero_rate, worst_regret)):
        right.text(
            min(rate + 0.01, 0.96),
            position,
            f"worst={worst:.2f}",
            va="center",
            fontsize=8,
        )
    fig.tight_layout()
    fig.savefig(output_dir / "synthetic_regret_tail.png", dpi=180)
    plt.close(fig)
