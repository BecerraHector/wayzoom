"""Screen capture through ``wf-recorder`` and region selection with ``slurp``."""

from __future__ import annotations

import logging
import re
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

_GEOMETRY = re.compile(r"^\s*(-?\d+),(-?\d+)\s+(\d+)x(\d+)\s*$")


@dataclass(frozen=True)
class CaptureTarget:
    """Either a whole output (``output``) or a logical region (``geometry``)."""

    output: str | None = None
    geometry: str | None = None


def parse_geometry(text: str) -> tuple[float, float, float, float]:
    """Parse slurp's ``"x,y wxh"`` into ``(x, y, w, h)``."""
    match = _GEOMETRY.match(text)
    if not match:
        raise ValueError(f"invalid geometry: {text!r}")
    x, y, w, h = (float(v) for v in match.groups())
    if w <= 0 or h <= 0:
        raise ValueError(f"empty geometry: {text!r}")
    return x, y, w, h


def select_region() -> str:
    """Let the user draw a region; return slurp's geometry string."""
    try:
        result = subprocess.run(["slurp"], capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise RuntimeError("slurp is not installed") from exc
    if result.returncode != 0:
        raise RuntimeError("region selection cancelled")
    geometry = result.stdout.strip()
    parse_geometry(geometry)
    return geometry


def recorder_command(
    target: CaptureTarget, output: Path, audio_device: str | None = None, fps: int = 60
) -> list[str]:
    command = ["wf-recorder", "-y"]
    if target.geometry:
        command += ["-g", target.geometry]
    elif target.output:
        command += ["-o", target.output]
    command += [
        "-f", str(output),
        "-r", str(fps),
        "-c", "libx264",
        "-p", "preset=ultrafast",
        "-p", "crf=18",
        "-x", "yuv420p",
    ]
    if audio_device:
        command.append(f"--audio={audio_device}")
    return command


class ScreenRecorder:
    """Owns one wf-recorder process."""

    def __init__(
        self, target: CaptureTarget, output: Path, audio_device: str | None, log_path: Path
    ) -> None:
        self.command = recorder_command(target, output, audio_device)
        self._log_path = log_path
        self._process: subprocess.Popen[bytes] | None = None

    def start(self) -> None:
        log.info("starting: %s", " ".join(self.command))
        with open(self._log_path, "ab") as log_file:
            self._process = subprocess.Popen(
                self.command,
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
            )

    def poll(self) -> int | None:
        return None if self._process is None else self._process.poll()

    def stop(self, timeout: float = 15.0) -> int | None:
        """SIGINT lets wf-recorder finalise the file; kill only as a last resort."""
        process = self._process
        if process is None:
            return None
        if process.poll() is None:
            process.send_signal(signal.SIGINT)
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                log.warning("wf-recorder did not exit after SIGINT; killing it")
                process.kill()
                process.wait()
        return process.returncode
