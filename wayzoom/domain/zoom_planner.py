"""Automatic zoom segment planning (DESIGN.md §3) and typing detection (DESIGN.md §4).

Keystrokes come from unmodified Hyprland key binds that record timestamps
only, so every stroke is a text character without modifiers.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

ZOOM_BEFORE_CLICK = 0.32
DEFAULT_DURATION = 1.266
MIN_SPACE = 2.6
ZOOM_OUT_BEFORE_END = 1.0
TYPING_LEAD = 0.5
DEFAULT_ZOOM = 2.0

IDLE_THRESHOLD_MS = 1500.0
MIN_DURATION_MS = 1500.0
MIN_STROKE_COUNT = 5
LEADING_ABSORB_GAP_MS = 2500.0
IGNORED_MODIFIERS = frozenset({"control", "command", "shift", "option", "alt", "function"})


@dataclass(frozen=True)
class ZoomSegment:
    begin: float
    end: float
    zoom: float


def plan_zoom_segments(
    click_times: Iterable[float],
    duration: float,
    zoom: float = DEFAULT_ZOOM,
    typing: Iterable[tuple[float, float]] = (),
) -> list[ZoomSegment]:
    """Build merged zoom segments from click times (seconds, video time)."""
    if zoom <= 0:
        zoom = DEFAULT_ZOOM
    limit = duration - ZOOM_OUT_BEFORE_END
    raw = [(start - TYPING_LEAD, end) for start, end in typing]
    raw += [(c - ZOOM_BEFORE_CLICK, c + DEFAULT_DURATION) for c in click_times]
    intervals: list[tuple[float, float]] = []
    for start, end in raw:
        start, end = max(0.0, start), min(limit, end)
        if start <= end:
            intervals.append((start, end))
    intervals.sort()
    merged: list[list[float]] = []
    for start, end in intervals:
        if merged and start <= merged[-1][1] + MIN_SPACE:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [ZoomSegment(start, end, zoom) for start, end in merged]


@dataclass(frozen=True)
class KeyStroke:
    """A key-down event: time in milliseconds, produced character, active modifiers."""

    t_ms: float
    character: str
    modifiers: frozenset[str] = frozenset()


def typing_ranges(
    strokes: Sequence[KeyStroke],
    idle_threshold_ms: float = IDLE_THRESHOLD_MS,
    min_duration_ms: float = MIN_DURATION_MS,
    min_stroke_count: int = MIN_STROKE_COUNT,
    leading_absorb_gap_ms: float = LEADING_ABSORB_GAP_MS,
) -> list[tuple[float, float]]:
    """Detect sustained typing intervals ``(start_ms, end_ms)``."""
    times = sorted(
        s.t_ms
        for s in strokes
        if s.character and not (s.modifiers & IGNORED_MODIFIERS)
    )
    if not times:
        return []
    fragments: list[list[float]] = [[times[0]]]
    for t in times[1:]:
        if t - fragments[-1][-1] > idle_threshold_ms:
            fragments.append([t])
        else:
            fragments[-1].append(t)

    def qualifies(fragment: list[float]) -> bool:
        return (
            fragment[-1] - fragment[0] >= min_duration_ms
            or len(fragment) >= min_stroke_count
        )

    result: list[tuple[float, float]] = []
    absorbed = False
    for index, fragment in enumerate(fragments):
        if not qualifies(fragment):
            continue
        start = fragment[0]
        if not absorbed and index > 0:
            previous = fragments[index - 1]
            if start - previous[-1] <= leading_absorb_gap_ms:
                start = previous[0]
        absorbed = True
        result.append((start, fragment[-1]))
    return result


PLACEHOLDER_CHARACTER = "x"


def key_typing_ranges(key_times: Iterable[float]) -> list[tuple[float, float]]:
    """Typing ranges in seconds from bare keystroke timestamps (seconds).

    The capture stores no key names, so each stroke gets a placeholder
    character and no modifiers before running :func:`typing_ranges`.
    """
    strokes = [KeyStroke(t * 1000.0, PLACEHOLDER_CHARACTER) for t in key_times]
    return [(start / 1000.0, end / 1000.0) for start, end in typing_ranges(strokes)]
