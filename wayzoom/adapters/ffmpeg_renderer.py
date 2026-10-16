"""FFmpeg/ffprobe access and the libplacebo crop filter graph.

libplacebo evaluates ``crop_*`` expressions of ``t`` per frame with sub-pixel
GPU sampling. ``sendcmd`` is not used: libplacebo acknowledges it but ignores it.
The (long) graph is loaded from a file with ``-/vf`` (FFmpeg 7+).
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path


class RenderError(RuntimeError):
    pass


@dataclass(frozen=True)
class VideoInfo:
    width: int
    height: int
    duration: float
    has_audio: bool


def probe(path: Path) -> VideoInfo:
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error", "-print_format", "json",
                "-show_format", "-show_streams", str(path),
            ],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise RenderError("ffprobe is not installed") from exc
    if result.returncode != 0:
        raise RenderError(f"ffprobe failed for {path}: {result.stderr.strip()}")
    data = json.loads(result.stdout)
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise RenderError(f"no video stream in {path}")
    duration = data.get("format", {}).get("duration") or video.get("duration")
    if duration is None:
        raise RenderError(f"unknown duration for {path}")
    return VideoInfo(
        width=int(video["width"]),
        height=int(video["height"]),
        duration=float(duration),
        has_audio=any(s.get("codec_type") == "audio" for s in streams),
    )


def has_libplacebo() -> bool:
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True
        )
    except FileNotFoundError:
        return False
    return any(line.split()[1:2] == ["libplacebo"] for line in result.stdout.splitlines())


def filter_graph(width: int, height: int, ex: str, ey: str, ew: str, eh: str) -> str:
    """Expressions are single-quoted because they contain commas."""
    for expr in (ex, ey, ew, eh):
        if "'" in expr:
            raise ValueError("expressions must not contain single quotes")
    return (
        f"setpts=PTS-STARTPTS,libplacebo=w={width}:h={height}"
        f":crop_x='{ex}':crop_y='{ey}':crop_w='{ew}':crop_h='{eh}'"
        f":format=yuv420p"
    )


def render_command(source: Path, filter_file: Path, output: Path, has_audio: bool) -> list[str]:
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-stats",
        "-i", str(source),
        "-/vf", str(filter_file),
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
    ]
    if has_audio:
        command += ["-c:a", "aac", "-b:a", "192k"]
    else:
        command += ["-an"]
    return command + ["-y", str(output)]


def render(source: Path, filter_file: Path, output: Path, has_audio: bool, log_path: Path | None = None) -> None:
    if not has_libplacebo():
        raise RenderError(
            "FFmpeg was built without the libplacebo filter; install an FFmpeg with "
            "libplacebo and Vulkan support"
        )
    command = render_command(source, filter_file, output, has_audio)
    result = subprocess.run(command, capture_output=True, text=True)
    if log_path is not None:
        log_path.write_text(" ".join(command) + "\n\n" + result.stderr)
    if result.returncode != 0:
        tail = "\n".join(result.stderr.strip().splitlines()[-10:])
        raise RenderError(f"ffmpeg failed ({result.returncode}):\n{tail}")
