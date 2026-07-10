"""The explicit equal-split first-best benchmark."""

from __future__ import annotations

from .domain import Profile, efficient_outcome
from .mechanism import Lottery, deterministic_lottery


class EqualSplitFirstBest:
    """Efficient allocation with the performer's reported cost split equally.

    This is a welfare/payment benchmark, not an incentive-compatible mechanism.
    If the chore is done, each roommate contributes c_k/n and performer k
    receives c_k. The transfers balance and therefore do not alter welfare.
    """

    name = "equal_split_first_best"

    def run(self, reports: Profile) -> Lottery:
        n = len(reports)
        performer = efficient_outcome(reports)
        if performer is None:
            return deterministic_lottery(None, (0.0,) * n)
        cost = reports[performer].cost
        transfers = [-cost / n] * n
        transfers[performer] += cost
        return deterministic_lottery(performer, tuple(transfers))
