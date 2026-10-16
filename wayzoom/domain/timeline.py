"""Uniform simulation timeline (DESIGN.md §5).

Every simulation step in WayZoom runs on a fixed 16 ms grid ``t_i = i * DT``.
"""

from __future__ import annotations

import bisect
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

DT = 0.016

Point = tuple[float, float]


@dataclass(frozen=True)
class CursorSample:
    """A cursor position at time ``t`` (seconds, video time, video pixels)."""

    t: float
    x: float
    y: float


@dataclass(frozen=True)
class Click:
    """A mouse button transition at time ``t`` (seconds, video time)."""

    t: float
    kind: str  # "down" or "up"
    button: int


def frame_count(duration: float) -> int:
    """Number of grid frames covering ``duration`` seconds: ``floor(D/DT) + 1``."""
    if duration <= 0:
        return 1
    # The small bias absorbs floating point error such as 6.0 / 0.016 = 374.99999.
    return math.floor(duration / DT + 1e-9) + 1


def frame_times(count: int) -> list[float]:
    """Times of the first ``count`` grid frames."""
    return [i * DT for i in range(count)]


def frame_index(t: float, count: int) -> int:
    """Grid index nearest ``t``, clamped to ``[0, count - 1]``."""
    return max(0, min(count - 1, round(t / DT)))


def resample_cursor(
    samples: Sequence[CursorSample],
    count: int,
    fallback: Point = (0.0, 0.0),
) -> list[Point]:
    """Linearly interpolate cursor samples onto the grid.

    Grid times before the first sample or after the last one take the
    position of that sample. Without samples every frame gets ``fallback``.
    """
    if not samples:
        return [fallback] * count
    ordered = sorted(samples, key=lambda s: s.t)
    times = [s.t for s in ordered]
    points: list[Point] = []
    for i in range(count):
        t = i * DT
        if t <= times[0]:
            points.append((ordered[0].x, ordered[0].y))
            continue
        if t >= times[-1]:
            points.append((ordered[-1].x, ordered[-1].y))
            continue
        hi = bisect.bisect_right(times, t)
        a, b = ordered[hi - 1], ordered[hi]
        span = b.t - a.t
        k = 0.0 if span <= 0 else (t - a.t) / span
        points.append((a.x + (b.x - a.x) * k, a.y + (b.y - a.y) * k))
    return points


def click_frames(click_times: Iterable[float], count: int) -> set[int]:
    """Grid indices nearest each click time."""
    return {frame_index(t, count) for t in click_times}
