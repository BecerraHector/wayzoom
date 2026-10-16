"""Desktop notifications through ``notify-send`` (best effort)."""

from __future__ import annotations

import shutil
import subprocess


def notify(summary: str, body: str = "", urgency: str = "normal") -> None:
    if shutil.which("notify-send") is None:
        return
    command = ["notify-send", "-a", "WayZoom", "-u", urgency, summary]
    if body:
        command.append(body)
    try:
        subprocess.run(command, timeout=5, capture_output=True)
    except (OSError, subprocess.SubprocessError):
        pass
