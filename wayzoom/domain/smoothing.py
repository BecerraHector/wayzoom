"""Cursor jitter smoothing (DESIGN.md §6)."""

from __future__ import annotations

import math
from collections.abc import Sequence, Set

from wayzoom.domain.timeline import Point

CLICK_ANCHOR_RADIUS = 4
AMPLITUDE_REFERENCE = 8.0
MIN_THRESHOLD = 100.0
MAX_THRESHOLD = 2000.0
DEFAULT_INTENSITY = 100.0


def jitter_score(points: Sequence[Point], times: Sequence[float], i: int) -> float:
    """Turn rate at frame ``i`` in deg/s, attenuated for large movements."""
    ax, ay = points[i][0] - points[i - 1][0], points[i][1] - points[i - 1][1]
    bx, by = points[i + 1][0] - points[i][0], points[i + 1][1] - points[i][1]
    len_a = math.hypot(ax, ay)
    len_b = math.hypot(bx, by)
    if len_a == 0 or len_b == 0:
        return 0.0
    cosine = max(-1.0, min(1.0, (ax * bx + ay * by) / (len_a * len_b)))
    angle = math.degrees(math.acos(cosine))
    span = max(times[i + 1] - times[i - 1], 1e-6)
    return angle / span * AMPLITUDE_REFERENCE / (AMPLITUDE_REFERENCE + min(len_a, len_b))


def _anchor_frames(count: int, clicks: Set[int]) -> set[int]:
    anchors = {0, count - 1}
    for c in clicks:
        for j in range(c - CLICK_ANCHOR_RADIUS, c + CLICK_ANCHOR_RADIUS + 1):
            if 0 <= j < count:
                anchors.add(j)
    return anchors


def smooth_path(
    points: Sequence[Point],
    times: Sequence[float],
    clicks: Set[int],
    intensity: float = DEFAULT_INTENSITY,
) -> list[Point]:
    """Replace jittery frames by time-linear interpolation of kept neighbours.

    Scores and interpolation always use the original positions.
    """
    count = len(points)
    if count < 3:
        return list(points)
    threshold = max(MIN_THRESHOLD, min(MAX_THRESHOLD, intensity))
    anchors = _anchor_frames(count, clicks)
    kept = [
        i in anchors or jitter_score(points, times, i) <= threshold for i in range(count)
    ]
    result = list(points)
    prev_kept = 0
    next_kept = [0] * count
    nxt = count - 1
    for i in range(count - 1, -1, -1):
        if kept[i]:
            nxt = i
        next_kept[i] = nxt
    for i in range(count):
        if kept[i]:
            prev_kept = i
            continue
        a, b = prev_kept, next_kept[i]
        span = times[b] - times[a]
        k = 0.0 if span <= 0 else (times[i] - times[a]) / span
        pa, pb = points[a], points[b]
        result[i] = (pa[0] + (pb[0] - pa[0]) * k, pa[1] + (pb[1] - pa[1]) * k)
    return result
