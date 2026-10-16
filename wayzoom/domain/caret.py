"""Substitute caret for typing zoom (DESIGN.md §12).

Wayland exposes no caret position, so the zoom target while typing is the
most recent click at or before the typing range (usually the text field);
without one it is the cursor at the start of the range.
"""

from __future__ import annotations

from collections.abc import Sequence

from wayzoom.domain.timeline import Point, frame_index
from wayzoom.domain.zoom_planner import TYPING_LEAD

ClickPoint = tuple[float, Point]  # (time in seconds, position in video px)

_EPS = 1e-9


def caret_point(range_start: float, clicks: Sequence[ClickPoint], cursor: Sequence[Point]) -> Point:
    """Position of the last click at or before ``range_start``, else the cursor there."""
    before = [c for c in clicks if c[0] <= range_start + _EPS]
    if before:
        return max(before, key=lambda c: c[0])[1]
    return cursor[frame_index(range_start, len(cursor))]


def effective_positions(
    cursor: Sequence[Point],
    times: Sequence[float],
    ranges: Sequence[tuple[float, float]],
    clicks: Sequence[ClickPoint],
) -> tuple[list[Point], list[bool]]:
    """Per-frame zoom target and whether it comes from the substitute caret.

    Frames with time in ``[start - TYPING_LEAD, end]`` of a typing range use
    that range's caret point; all other frames keep the cursor.
    """
    positions = list(cursor)
    uses_caret = [False] * len(cursor)
    for start, end in ranges:
        point = caret_point(start, clicks, cursor)
        low = start - TYPING_LEAD - _EPS
        high = end + _EPS
        for i, t in enumerate(times):
            if low <= t <= high:
                positions[i] = point
                uses_caret[i] = True
    return positions, uses_caret
