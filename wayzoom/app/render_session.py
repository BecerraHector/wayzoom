"""Turn a recorded session into a zoomed video.

Pipeline (sections of DESIGN.md): load telemetry -> align to video time -> resample (§5) -> smooth (§6)
-> cursor spring (§7) -> typing ranges (§4) -> plan segments (§3) -> scale
spring (§8) -> substitute caret (§12) -> anchors (§9, §10) -> crop rectangles
-> FFmpeg expressions -> libplacebo render.
"""

from __future__ import annotations

import csv
import json
import logging
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path

from wayzoom.adapters import ffmpeg_renderer
from wayzoom.adapters.ffmpeg_renderer import VideoInfo
from wayzoom.domain.caret import effective_positions
from wayzoom.domain.camera import CropRect, crop_rects, spring_anchor, spring_scale
from wayzoom.domain.cursor_motion import spring_cursor
from wayzoom.domain.expression import DEFAULT_EPSILON, series_expression
from wayzoom.domain.smoothing import DEFAULT_INTENSITY, smooth_path
from wayzoom.domain.spring import DEFAULT_CURSOR_PRESET, DEFAULT_ZOOM_PRESET, PRESETS
from wayzoom.domain.timeline import (
    Click,
    CursorSample,
    click_frames,
    frame_count,
    frame_index,
    frame_times,
    resample_cursor,
)
from wayzoom.domain.zoom_planner import (
    DEFAULT_ZOOM,
    ZoomSegment,
    key_typing_ranges,
    plan_zoom_segments,
)

log = logging.getLogger(__name__)

RAW_VIDEO = "raw.mkv"
CURSOR_FILE = "cursor.csv"
CLICKS_FILE = "clicks.csv"
KEYS_FILE = "keys.csv"
META_FILE = "meta.json"
FILTER_FILE = "filter.txt"


@dataclass(frozen=True)
class RenderOptions:
    zoom: float = DEFAULT_ZOOM
    zoom_speed: str = DEFAULT_ZOOM_PRESET
    cursor_speed: str = DEFAULT_CURSOR_PRESET
    smoothing: bool = True
    smoothing_intensity: float = DEFAULT_INTENSITY
    epsilon: float = DEFAULT_EPSILON
    typing_zoom: bool | None = None  # None follows the session's recording arguments

    def typing_enabled(self, recorded: bool) -> bool:
        return recorded if self.typing_zoom is None else self.typing_zoom


@dataclass(frozen=True)
class CaptureRect:
    """Captured area in logical (compositor) coordinates."""

    x: float
    y: float
    w: float
    h: float


@dataclass
class SessionData:
    rect: CaptureRect
    t_stop_mono: float
    cursor: list[tuple[float, float, float]] = field(default_factory=list)
    clicks: list[tuple[float, str, int]] = field(default_factory=list)
    keys: list[float] = field(default_factory=list)
    typing_zoom: bool = True


def _rows(path: Path) -> list[list[str]]:
    if not path.exists():
        return []
    with path.open(newline="") as handle:
        return [row for row in csv.reader(handle) if row and not row[0].startswith("t_")]


def load_session(session_dir: Path) -> SessionData:
    meta = json.loads((session_dir / META_FILE).read_text())
    capture = meta["capture"]
    session = SessionData(
        rect=CaptureRect(
            float(capture["x"]), float(capture["y"]),
            float(capture["width"]), float(capture["height"]),
        ),
        t_stop_mono=float(meta["t_stop_mono"]),
        typing_zoom=bool((meta.get("args") or {}).get("typing_zoom", True)),
    )
    for row in _rows(session_dir / CURSOR_FILE):
        try:
            session.cursor.append((float(row[0]), float(row[1]), float(row[2])))
        except (IndexError, ValueError):
            log.warning("skipping malformed cursor row %r", row)
    for row in _rows(session_dir / CLICKS_FILE):
        try:
            session.clicks.append((float(row[0]), row[1], int(row[2])))
        except (IndexError, ValueError):
            log.warning("skipping malformed click row %r", row)
    for row in _rows(session_dir / KEYS_FILE):
        try:
            session.keys.append(float(row[0]))
        except (IndexError, ValueError):
            log.warning("skipping malformed key row %r", row)
    return session


def align_to_video(
    session: SessionData, info: VideoInfo
) -> tuple[list[CursorSample], list[Click]]:
    """Convert monotonic logical-coordinate telemetry to video time and pixels.

    The recorder stops at ``t_stop_mono``, so the video starts at
    ``t_stop_mono - duration``.
    """
    start = session.t_stop_mono - info.duration
    sx = info.width / session.rect.w
    sy = info.height / session.rect.h
    samples = [
        CursorSample(t - start, (x - session.rect.x) * sx, (y - session.rect.y) * sy)
        for t, x, y in session.cursor
    ]
    clicks = [Click(t - start, kind, button) for t, kind, button in session.clicks]
    return samples, clicks


def align_keys(session: SessionData, info: VideoInfo) -> list[float]:
    """Keystroke times in video seconds."""
    start = session.t_stop_mono - info.duration
    return [t - start for t in session.keys]


@dataclass
class CameraPlan:
    duration: float
    times: list[float]
    segments: list[ZoomSegment]
    scale: list[float]
    anchors: list[tuple[float, float]]
    crops: list[CropRect]
    typing_ranges: list[tuple[float, float]] = field(default_factory=list)


def plan_camera(
    samples: Sequence[CursorSample],
    clicks: Sequence[Click],
    info: VideoInfo,
    options: RenderOptions,
    key_times: Sequence[float] = (),
) -> CameraPlan:
    """Plan the camera; ``key_times`` (video seconds) enable typing zoom."""
    count = frame_count(info.duration)
    times = frame_times(count)
    width, height = float(info.width), float(info.height)
    raw = resample_cursor(samples, count, fallback=(width / 2, height / 2))
    click_times = [c.t for c in clicks]
    click_points = [(c.t, raw[frame_index(c.t, count)]) for c in clicks if c.kind == "down"]
    if options.smoothing:
        raw = smooth_path(raw, times, click_frames(click_times, count), options.smoothing_intensity)
    cursor = spring_cursor(raw, PRESETS[options.cursor_speed])
    typing = key_typing_ranges(key_times)
    segments = plan_zoom_segments(click_times, info.duration, options.zoom, typing)
    zoom_params = PRESETS[options.zoom_speed]
    scale = spring_scale(segments, count, zoom_params)
    positions, uses_caret = effective_positions(cursor, times, typing, click_points)
    anchors = spring_anchor(segments, positions, scale, width, height, zoom_params, uses_caret)
    crops = crop_rects(scale, anchors, width, height)
    return CameraPlan(info.duration, times, segments, scale, anchors, crops, typing)


def crop_expressions(plan: CameraPlan, epsilon: float = DEFAULT_EPSILON) -> tuple[str, str, str, str]:
    return tuple(  # type: ignore[return-value]
        series_expression(plan.times, [getattr(c, axis) for c in plan.crops], epsilon)
        for axis in ("x", "y", "w", "h")
    )


def plan_summary(plan: CameraPlan) -> dict[str, object]:
    return {
        "duration": round(plan.duration, 4),
        "frames": len(plan.times),
        "segments": [
            {k: round(v, 4) for k, v in asdict(segment).items()} for segment in plan.segments
        ],
        "typing_ranges": [[round(start, 4), round(end, 4)] for start, end in plan.typing_ranges],
    }


def default_output(session_dir: Path) -> Path:
    """``.../wayzoom/.sessions/<ts>`` renders to ``.../wayzoom/wayzoom-<ts>.mp4``."""
    return session_dir.resolve().parent.parent / f"wayzoom-{session_dir.name}.mp4"


def prepare(session_dir: Path, options: RenderOptions) -> tuple[VideoInfo, CameraPlan]:
    info = ffmpeg_renderer.probe(session_dir / RAW_VIDEO)
    session = load_session(session_dir)
    samples, clicks = align_to_video(session, info)
    keys = align_keys(session, info) if options.typing_enabled(session.typing_zoom) else []
    return info, plan_camera(samples, clicks, info, options, keys)


def render_session(session_dir: Path, output: Path, options: RenderOptions) -> Path:
    info, plan = prepare(session_dir, options)
    ex, ey, ew, eh = crop_expressions(plan, options.epsilon)
    filter_file = session_dir / FILTER_FILE
    filter_file.write_text(ffmpeg_renderer.filter_graph(info.width, info.height, ex, ey, ew, eh))
    log.info("plan: %s", json.dumps(plan_summary(plan)))
    output.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg_renderer.render(
        session_dir / RAW_VIDEO, filter_file, output, info.has_audio,
        log_path=session_dir / "ffmpeg.log",
    )
    return output
