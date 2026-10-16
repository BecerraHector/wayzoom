"""Virtual camera (DESIGN.md): zoom scale spring (§8), dwell-cluster anchors (§9),
edge clamping (§10) and the per-frame crop rectangle."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from wayzoom.domain.spring import (
    DEFAULT_ZOOM_PRESET,
    PRESETS,
    SpringParams,
    SpringPoint,
    SpringValue,
)
from wayzoom.domain.timeline import Point, frame_index
from wayzoom.domain.zoom_planner import ZoomSegment

INSTANT_SCALE_TOLERANCE = 0.01
MIN_ANCHOR_SCALE = 1.01
CENTRE: Point = (0.5, 0.5)


def get_anchor_point(scale: float, target: Point) -> Point:
    """Scale-about anchor that shows ``target`` centred, clamped to stay on screen."""
    if scale <= 1:
        return target
    k = scale / (scale - 1)
    half = 1 / (2 * scale)
    return (
        max(0.0, min(1.0, k * (target[0] - half))),
        max(0.0, min(1.0, k * (target[1] - half))),
    )


def _segment_indices(segment: ZoomSegment, count: int) -> tuple[int, int]:
    return frame_index(segment.begin, count), frame_index(segment.end, count)


def scale_targets(segments: Sequence[ZoomSegment], count: int) -> list[tuple[int, float]]:
    """Step targets ``(frame index, scale)`` for the scale spring."""
    targets: list[tuple[int, float]] = []
    for segment in sorted(segments, key=lambda s: s.begin):
        start, end = _segment_indices(segment, count)
        if (not targets or targets[-1][0] < start) and start > 0:
            targets.append((start, 1.0))
        targets.append((end, segment.zoom))
    targets.append((count - 1, 1.0))
    return targets


def spring_scale(
    segments: Sequence[ZoomSegment],
    count: int,
    params: SpringParams = PRESETS[DEFAULT_ZOOM_PRESET],
) -> list[float]:
    """Per-frame zoom scale."""
    if not segments:
        return [1.0] * count
    pending = scale_targets(segments, count)
    current = pending.pop(0)
    spring = SpringValue(current[1], params=params.regulated())
    out: list[float] = []
    for i in range(count):
        if i > current[0] and pending:
            current = pending.pop(0)
        out.append(max(spring.step(current[1]), 0.0))
    return out


@dataclass
class AnchorTarget:
    index: int
    point: Point
    instant: bool


def anchor_targets(
    segments: Sequence[ZoomSegment],
    positions: Sequence[Point],
    scale: Sequence[float],
    width: float,
    height: float,
    uses_caret: Sequence[bool] | None = None,
) -> list[AnchorTarget]:
    """Group zoom target positions inside each segment into dwell clusters.

    ``uses_caret`` marks frames whose position is the (substitute) caret; a
    cluster also closes when the source switches between cursor and caret.
    """
    count = len(positions)
    flags = list(uses_caret) if uses_caret is not None else [False] * count
    entries: list[AnchorTarget] = []
    for segment in sorted(segments, key=lambda s: s.begin):
        start, end = _segment_indices(segment, count)
        if entries:
            entries[-1].index = start
        zoom = segment.zoom
        half_w = width / zoom * 0.5
        half_h = height / zoom * 0.5
        ref = positions[start]
        min_x = max_x = ref[0]
        min_y = max_y = ref[1]
        emitted = 0
        last_emit: int | None = None

        def emit(index: int) -> None:
            nonlocal emitted, last_emit
            centre = ((min_x + max_x) / 2 / width, (min_y + max_y) / 2 / height)
            point = get_anchor_point(max(MIN_ANCHOR_SCALE, zoom), centre)
            instant = emitted == 0 and abs(scale[start] - 1.0) < INSTANT_SCALE_TOLERANCE
            entries.append(AnchorTarget(index, point, instant))
            last_emit = index
            emitted += 1

        for i in range(start, end):
            px, py = positions[i]
            if i > start and flags[i] != flags[i - 1]:
                if last_emit != i - 1:
                    emit(i - 1)
                ref = (px, py)
                min_x = max_x = px
                min_y = max_y = py
            min_x, max_x = min(min_x, px), max(max_x, px)
            min_y, max_y = min(min_y, py), max(max_y, py)
            closes = (
                abs(px - ref[0]) > half_w
                or abs(py - ref[1]) > half_h
                or max_x - min_x > half_w
                or max_y - min_y > half_h
                or i == end - 1
            )
            if not closes:
                continue
            if last_emit != i:
                emit(i)
            ref = (px, py)
            min_x = max_x = px
            min_y = max_y = py
    return entries


def spring_anchor(
    segments: Sequence[ZoomSegment],
    positions: Sequence[Point],
    scale: Sequence[float],
    width: float,
    height: float,
    params: SpringParams = PRESETS[DEFAULT_ZOOM_PRESET],
    uses_caret: Sequence[bool] | None = None,
) -> list[Point]:
    """Per-frame normalized anchor point."""
    count = len(positions)
    pending = anchor_targets(segments, positions, scale, width, height, uses_caret)
    if not pending:
        return [CENTRE] * count
    current = pending.pop(0)
    spring = SpringPoint.at(current.point, params)
    out: list[Point] = []
    for i in range(count):
        if i > current.index and pending:
            current = pending.pop(0)
        if current.instant:
            spring.snap(current.point)
        else:
            spring.step(current.point)
        out.append(spring.position)
    return out


@dataclass(frozen=True)
class CropRect:
    x: float
    y: float
    w: float
    h: float


def crop_rect(scale: float, anchor: Point, width: float, height: float) -> CropRect:
    """Source rectangle shown for one frame.

    Scales below 1.0 would need a background (other recorders draw one); they are
    rendered as the full frame here. The anchor is clamped to ``[0, 1]`` so
    that spring overshoot can never move the crop off screen.
    """
    s = max(scale, 1.0)
    w, h = width / s, height / s
    ax = max(0.0, min(1.0, anchor[0]))
    ay = max(0.0, min(1.0, anchor[1]))
    x, y = ax * (width - w), ay * (height - h)
    assert 0.0 <= x <= width - w + 1e-9 and 0.0 <= y <= height - h + 1e-9
    return CropRect(x, y, w, h)


def crop_rects(
    scale: Sequence[float],
    anchors: Sequence[Point],
    width: float,
    height: float,
) -> list[CropRect]:
    return [crop_rect(s, a, width, height) for s, a in zip(scale, anchors, strict=True)]
