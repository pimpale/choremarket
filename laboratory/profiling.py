"""Lightweight wall-clock phase timing shared across the laboratory.

Labels are designed to be disjoint leaves: callers time only their own work
and let self-instrumenting callees (the LP solvers) report their internal
stages, so the report's shares are meaningful without double counting.
"""

from __future__ import annotations

from contextlib import contextmanager
from time import perf_counter
from typing import Callable, Iterator


class PhaseTimer:
    """Accumulates named phase durations in first-seen order."""

    def __init__(self) -> None:
        self._totals: dict[str, float] = {}
        self._calls: dict[str, int] = {}

    def add(self, label: str, seconds: float) -> None:
        self._totals[label] = self._totals.get(label, 0.0) + seconds
        self._calls[label] = self._calls.get(label, 0) + 1

    @contextmanager
    def phase(self, label: str) -> Iterator[None]:
        start = perf_counter()
        try:
            yield
        finally:
            self.add(label, perf_counter() - start)

    def rows(self) -> list[dict[str, float | int | str]]:
        total = sum(self._totals.values())
        return [
            {
                "phase": label,
                "calls": self._calls[label],
                "seconds": seconds,
                "share_of_timed": seconds / total if total else 0.0,
            }
            for label, seconds in self._totals.items()
        ]

    def report(self) -> str:
        lines = ["Phase timings (chronological)"]
        for row in self.rows():
            lines.append(
                f"  {row['phase']:<52s} {row['seconds']:9.2f}s"
                f"  {row['share_of_timed']:6.1%}  x{row['calls']}"
            )
        return "\n".join(lines)

    def reset(self) -> None:
        self._totals.clear()
        self._calls.clear()


TIMER = PhaseTimer()


def timed(label: str):
    """Time a block against the shared experiment timer."""

    return TIMER.phase(label)


def checkpoint() -> Callable[[str], None]:
    """Sequential marks for linear code: each call times since the last one."""

    last = perf_counter()

    def mark(label: str) -> None:
        nonlocal last
        now = perf_counter()
        TIMER.add(label, now - last)
        last = now

    return mark
