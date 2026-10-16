"""Hyprland IPC: request socket, event socket, monitors, click and key binds.

Hyprland 0.56 with a Lua configuration rejects ``hyprctl keyword``; binds are
installed at runtime with ``hyprctl eval '<lua>'`` instead.
"""

from __future__ import annotations

import json
import logging
import os
import shlex
import socket
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from wayzoom.domain.timeline import DT

log = logging.getLogger(__name__)

LEFT_BUTTON = 272
RIGHT_BUTTON = 273
CLICK_BUTTONS = (LEFT_BUTTON, RIGHT_BUTTON)

# Unmodified text keysyms bound while recording for typing detection (DESIGN.md §4).
TYPING_KEYS: tuple[str, ...] = (
    tuple(chr(c) for c in range(ord("a"), ord("z") + 1))
    + tuple(str(d) for d in range(10))
    + ("space", "ntilde", "comma", "period", "minus")
)


class HyprlandUnavailable(RuntimeError):
    """Raised when no Hyprland instance can be reached."""


def socket_dir() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    signature = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    if not runtime or not signature:
        raise HyprlandUnavailable(
            "HYPRLAND_INSTANCE_SIGNATURE or XDG_RUNTIME_DIR is not set; is Hyprland running?"
        )
    return Path(runtime) / "hypr" / signature


def request(command: str, timeout: float = 1.0) -> bytes:
    """Send one request on ``.socket.sock``; the server closes after replying."""
    path = socket_dir() / ".socket.sock"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        try:
            sock.connect(str(path))
        except OSError as exc:
            raise HyprlandUnavailable(f"cannot connect to {path}: {exc}") from exc
        sock.sendall(command.encode())
        chunks = []
        while chunk := sock.recv(65536):
            chunks.append(chunk)
    return b"".join(chunks)


def request_json(command: str) -> object:
    return json.loads(request(f"j/{command}"))


def cursor_position() -> tuple[float, float]:
    """Cursor position in logical global coordinates."""
    data = request_json("cursorpos")
    assert isinstance(data, dict)
    return float(data["x"]), float(data["y"])


@dataclass(frozen=True)
class Monitor:
    name: str
    width: int
    height: int
    x: int
    y: int
    scale: float
    transform: int
    focused: bool

    def logical_rect(self) -> tuple[float, float, float, float]:
        """``(x, y, w, h)`` in logical coordinates, honouring rotation."""
        w, h = self.width / self.scale, self.height / self.scale
        if self.transform in (1, 3, 5, 7):
            w, h = h, w
        return float(self.x), float(self.y), w, h


def parse_monitors(data: object) -> list[Monitor]:
    assert isinstance(data, list)
    return [
        Monitor(
            name=str(m["name"]),
            width=int(m["width"]),
            height=int(m["height"]),
            x=int(m["x"]),
            y=int(m["y"]),
            scale=float(m.get("scale", 1.0)) or 1.0,
            transform=int(m.get("transform", 0)),
            focused=bool(m.get("focused", False)),
        )
        for m in data
    ]


def monitors() -> list[Monitor]:
    return parse_monitors(request_json("monitors"))


def focused_monitor() -> Monitor:
    found = monitors()
    if not found:
        raise HyprlandUnavailable("Hyprland reported no monitors")
    return next((m for m in found if m.focused), found[0])


def monitor_containing(x: float, y: float) -> Monitor | None:
    for m in monitors():
        mx, my, mw, mh = m.logical_rect()
        if mx <= x < mx + mw and my <= y < my + mh:
            return m
    return None


# --- click binds ---------------------------------------------------------


def lua_string(text: str) -> str:
    """Quote ``text`` as a double-quoted Lua string literal."""
    escaped = (
        text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r")
    )
    return f'"{escaped}"'


def click_command(fifo: Path, kind: str, button: int) -> str:
    """Shell command that appends one click line to the FIFO.

    ``timeout`` guards against a stale FIFO without reader, where opening it
    for writing would block forever.
    """
    if kind not in ("down", "up"):
        raise ValueError(f"invalid click kind: {kind!r}")
    inner = f"echo {kind} {int(button)} >> {shlex.quote(str(fifo))}"
    return f"timeout 1 sh -c {shlex.quote(inner)}"


def bind_lua(button: int, command: str, release: bool) -> str:
    options = "non_consuming = true" + (", release = true" if release else "")
    return f'hl.bind("mouse:{button}", hl.dsp.exec_cmd({lua_string(command)}), {{ {options} }})'


def unbind_lua(button: int) -> str:
    return f'hl.unbind("mouse:{button}")'


def eval_lua(code: str) -> str:
    """Run Lua through ``hyprctl eval``; raise when Hyprland reports an error."""
    try:
        result = subprocess.run(
            ["hyprctl", "eval", code], capture_output=True, text=True, timeout=5
        )
    except FileNotFoundError as exc:
        raise HyprlandUnavailable("hyprctl not found") from exc
    output = (result.stdout + result.stderr).strip()
    if result.returncode != 0 or "error" in output.lower():
        raise RuntimeError(f"hyprctl eval failed ({result.returncode}): {output}")
    return output


def click_bind_statements(fifo: Path) -> list[str]:
    return [
        bind_lua(LEFT_BUTTON, click_command(fifo, "down", LEFT_BUTTON), release=False),
        bind_lua(LEFT_BUTTON, click_command(fifo, "up", LEFT_BUTTON), release=True),
        bind_lua(RIGHT_BUTTON, click_command(fifo, "down", RIGHT_BUTTON), release=False),
    ]


def install_click_binds(fifo: Path) -> None:
    for statement in click_bind_statements(fifo):
        eval_lua(statement)


def remove_click_binds() -> None:
    """Remove the unmodified mouse binds; modified binds (e.g. SUPER+drag) survive."""
    errors = []
    for button in CLICK_BUTTONS:
        try:
            eval_lua(unbind_lua(button))
        except (RuntimeError, HyprlandUnavailable) as exc:
            errors.append(str(exc))
    if errors:
        raise RuntimeError("; ".join(errors))


# --- key binds -----------------------------------------------------------


def key_command(fifo: Path) -> str:
    """Shell command that appends a bare ``key`` line to the FIFO.

    The key name is deliberately not written: only keystroke timestamps are
    ever recorded.
    """
    inner = f"echo key >> {shlex.quote(str(fifo))}"
    return f"timeout 1 sh -c {shlex.quote(inner)}"


def _check_key(key: str) -> str:
    if key not in TYPING_KEYS:
        raise ValueError(f"not a typing key: {key!r}")
    return key


def key_bind_lua(key: str, command: str) -> str:
    return f'hl.bind("{_check_key(key)}", hl.dsp.exec_cmd({lua_string(command)}), {{ non_consuming = true }})'


def key_unbind_lua(key: str) -> str:
    """Remove every unmodified bind on ``key``; modified binds survive."""
    return f'hl.unbind("{_check_key(key)}")'


def bindable_keys(binds: object, keys: tuple[str, ...] = TYPING_KEYS) -> tuple[list[str], list[str]]:
    """Split ``keys`` into ``(bindable, skipped)`` given ``j/binds`` output.

    A key is skipped when any submap already has an unmodified bind on it,
    because ``hl.unbind`` would remove that user bind together with ours.
    """
    taken: set[str] = set()
    if isinstance(binds, list):
        for bind in binds:
            if isinstance(bind, dict) and int(bind.get("modmask", -1)) == 0:
                name = str(bind.get("key", "")).lower()
                if name:
                    taken.add(name)
    bindable = [k for k in keys if k not in taken]
    skipped = [k for k in keys if k in taken]
    return bindable, skipped


def install_key_binds(
    fifo: Path, on_planned: Callable[[list[str]], None] | None = None
) -> list[str]:
    """Bind the free typing keys; return the keys actually bound.

    ``on_planned`` receives the keys about to be bound before any bind is
    installed, so that a crash mid-way still leaves a record for cleanup.
    """
    keys, skipped = bindable_keys(request_json("binds"))
    if skipped:
        log.warning("typing keys with existing unmodified binds are not captured: %s", " ".join(skipped))
    if on_planned is not None:
        on_planned(list(keys))
    command = key_command(fifo)
    bound = []
    for key in keys:
        try:
            eval_lua(key_bind_lua(key, command))
            bound.append(key)
        except RuntimeError as exc:
            log.warning("cannot bind %s: %s", key, exc)
    return bound


def remove_key_binds(keys: list[str]) -> None:
    """Unbind ``keys``; names outside :data:`TYPING_KEYS` are ignored."""
    errors = []
    for key in keys:
        if key not in TYPING_KEYS:
            log.warning("ignoring unknown key name %r", key)
            continue
        try:
            eval_lua(key_unbind_lua(key))
        except (RuntimeError, HyprlandUnavailable) as exc:
            errors.append(f"{key}: {exc}")
    if errors:
        raise RuntimeError("; ".join(errors))


# --- background threads --------------------------------------------------


class CursorPoller(threading.Thread):
    """Samples the cursor every ``interval`` seconds on a monotonic schedule."""

    def __init__(
        self,
        sink: Callable[[float, float, float], None],
        interval: float = DT,
    ) -> None:
        super().__init__(name="cursor-poller", daemon=True)
        self._sink = sink
        self._interval = interval
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        next_tick = time.monotonic()
        failures = 0
        while not self._stop.is_set():
            try:
                x, y = cursor_position()
                self._sink(time.monotonic(), x, y)
                failures = 0
            except (OSError, ValueError, KeyError, HyprlandUnavailable) as exc:
                failures += 1
                if failures in (1, 100):
                    log.warning("cursor poll failed: %s", exc)
            next_tick += self._interval
            delay = next_tick - time.monotonic()
            if delay < 0:  # fell behind; resynchronise instead of bursting
                next_tick = time.monotonic()
                delay = 0
            self._stop.wait(delay)


class EventListener(threading.Thread):
    """Reads ``.socket2.sock`` line events and forwards ``(t_mono, line)``."""

    def __init__(self, sink: Callable[[float, str], None]) -> None:
        super().__init__(name="hypr-events", daemon=True)
        self._sink = sink
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        try:
            path = socket_dir() / ".socket2.sock"
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.connect(str(path))
        except (OSError, HyprlandUnavailable) as exc:
            log.warning("event socket unavailable: %s", exc)
            return
        sock.settimeout(0.2)
        buffer = b""
        with sock:
            while not self._stop.is_set():
                try:
                    chunk = sock.recv(65536)
                except TimeoutError:
                    continue
                except OSError as exc:
                    log.warning("event socket error: %s", exc)
                    return
                if not chunk:
                    return
                now = time.monotonic()
                buffer += chunk
                *lines, buffer = buffer.split(b"\n")
                for line in lines:
                    if line:
                        self._sink(now, line.decode(errors="replace"))
