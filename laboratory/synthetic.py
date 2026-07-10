"""Reproducible household-calibrated bid and WTP profiles."""

from __future__ import annotations

import csv
import math
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .domain import Profile, Type


# WTP anchors and observed winning asks come from the anonymized household
# spreadsheet already described in backend/app/repository.py. Keeping a copy in
# the lab makes experiments independent of the web app.
CHORE_ANCHORS: dict[str, tuple[float, tuple[float, ...], float]] = {
    "trash_recycling": (30.0, (6, 12, 12, 13, 13, 13, 13, 19), 0.22),
    "put_away_dishes": (20.0, (8, 11, 13, 13, 13, 14, 14, 15, 16), 0.25),
    "kitchen_surfaces": (12.0, (6, 7, 7, 10, 10, 12, 12, 12), 0.16),
    "vacuum_downstairs": (10.0, (11, 13, 13, 13, 13, 13, 13), 0.14),
    "clean_sink": (5.0, (3,), 0.08),
    "clean_microwave": (5.0, (3, 6), 0.07),
    "clean_bathrooms": (18.0, (51,), 0.08),
}


@dataclass(frozen=True)
class SyntheticProfile:
    scenario_id: int
    chore: str
    types: Profile


def _nearest(value: float, levels: tuple[float, ...]) -> float:
    return min(levels, key=lambda level: (abs(level - value), level))


def generate_profiles(
    n: int,
    count: int,
    seed: int = 20260710,
) -> list[SyntheticProfile]:
    """Draw correlated, nonnegative values and effort costs.

    Each roommate receives persistent cleanliness and reluctance traits. Chore
    shocks then add within-person variation, while observed asks anchor costs.
    """

    if n < 2 or count <= 0:
        raise ValueError("n must be at least two and count must be positive")
    rng = random.Random(seed)
    cleanliness = [rng.lognormvariate(-0.5 * 0.25**2, 0.25) for _ in range(n)]
    reluctance = [rng.lognormvariate(-0.5 * 0.30**2, 0.30) for _ in range(n)]
    chores = tuple(CHORE_ANCHORS)
    weights = tuple(CHORE_ANCHORS[chore][2] for chore in chores)
    generated = []
    for scenario_id in range(count):
        chore = rng.choices(chores, weights=weights, k=1)[0]
        wtp_anchor, observed_asks, _ = CHORE_ANCHORS[chore]
        sorted_asks = sorted(observed_asks)
        cost_anchor = sorted_asks[len(sorted_asks) // 2]
        types = []
        for i in range(n):
            common_shock = rng.lognormvariate(-0.5 * 0.10**2, 0.10)
            value = wtp_anchor * cleanliness[i] * common_shock
            cost = cost_anchor * reluctance[i] * common_shock
            value *= rng.lognormvariate(-0.5 * 0.14**2, 0.14)
            cost *= rng.lognormvariate(-0.5 * 0.18**2, 0.18)
            types.append(Type(round(max(0.0, value), 2), round(max(0.0, cost), 2)))
        generated.append(SyntheticProfile(scenario_id, chore, tuple(types)))
    return generated


def quantize_profile(
    profile: Profile,
    value_levels: Iterable[float],
    cost_levels: Iterable[float],
) -> Profile:
    values = tuple(map(float, value_levels))
    costs = tuple(map(float, cost_levels))
    return tuple(Type(_nearest(t.value, values), _nearest(t.cost, costs)) for t in profile)


def write_profiles_csv(
    profiles: Iterable[SyntheticProfile],
    path: str | Path,
    value_levels: Iterable[float],
    cost_levels: Iterable[float],
    fine_value_levels: Iterable[float] | None = None,
) -> None:
    """Write long-form raw and grid-quantized bids/WTPs."""

    value_levels = tuple(map(float, value_levels))
    cost_levels = tuple(map(float, cost_levels))
    fine_values = (
        None if fine_value_levels is None else tuple(map(float, fine_value_levels))
    )
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        fieldnames = [
            "scenario_id",
            "chore",
            "roommate",
            "wtp_dollars",
            "bid_dollars",
            "grid_wtp",
            "grid_bid",
        ]
        if fine_values is not None:
            fieldnames.append("fine_demand_grid_wtp")
        writer = csv.DictWriter(
            handle,
            lineterminator="\n",
            fieldnames=fieldnames,
        )
        writer.writeheader()
        for sample in profiles:
            quantized = quantize_profile(sample.types, value_levels, cost_levels)
            for i, (raw, grid) in enumerate(zip(sample.types, quantized), start=1):
                row = {
                    "scenario_id": sample.scenario_id,
                    "chore": sample.chore,
                    "roommate": i,
                    "wtp_dollars": raw.value,
                    "bid_dollars": raw.cost,
                    "grid_wtp": grid.value,
                    "grid_bid": grid.cost,
                }
                if fine_values is not None:
                    row["fine_demand_grid_wtp"] = _nearest(raw.value, fine_values)
                writer.writerow(row)
