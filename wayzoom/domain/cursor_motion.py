"""Cursor spring (DESIGN.md §7): position only; tilt and motion blur are out of scope."""

from __future__ import annotations

from collections.abc import Sequence

from wayzoom.domain.spring import DEFAULT_CURSOR_PRESET, PRESETS, SpringParams, SpringPoint
from wayzoom.domain.timeline import Point


def spring_cursor(
    points: Sequence[Point],
    params: SpringParams = PRESETS[DEFAULT_CURSOR_PRESET],
) -> list[Point]:
    """Follow ``points`` with a 2D spring that starts at rest on the first point."""
    if not points:
        return []
    spring = SpringPoint.at(points[0], params)
    return [spring.step(p) for p in points]
