"""Desktop and microphone audio capture through PulseAudio-compatible ``pactl``.

Modes:

- ``desktop`` records the default sink's monitor (what the speakers play).
- ``mic`` records the default source (the microphone).
- ``both`` loads a temporary null sink ``wayzoom_mix`` and two loopbacks
  (default sink monitor -> mix, default source -> mix) and records the mix's
  monitor. The default sink and source are never changed.
- ``none`` records no audio.

Loaded module ids are appended to a runtime file as soon as each one loads, so
a daemon killed mid-recording leaves a record that the next start (or
``wayzoom cleanup``) uses to unload them.
"""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

MODES = ("both", "desktop", "mic", "none")
DEFAULT_MODE = "both"
MIX_SINK = "wayzoom_mix"
MIX_DESCRIPTION = "WayZoom-mix"
LOOPBACK_LATENCY_MS = 20
PACTL_TIMEOUT = 5.0


class AudioError(RuntimeError):
    pass


Runner = Callable[[list[str]], str]
"""Runs ``pactl`` with the given arguments; returns stdout or raises AudioError."""


def run_pactl(args: list[str]) -> str:
    try:
        result = subprocess.run(
            ["pactl", *args], capture_output=True, text=True, timeout=PACTL_TIMEOUT,
            stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError as exc:
        raise AudioError("pactl is not installed") from exc
    except subprocess.TimeoutExpired as exc:
        raise AudioError(f"pactl {' '.join(args)} timed out") from exc
    if result.returncode != 0:
        raise AudioError(f"pactl {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


# --- pure helpers ----------------------------------------------------------


def normalize_mode(value: object) -> str:
    """Map a requested mode (or a legacy boolean ``audio`` flag) to a mode."""
    if value is True:
        return "mic"  # the old ``--audio`` recorded the default source
    if value is False or value is None:
        return "none"
    if value in MODES:
        return str(value)
    raise ValueError(f"unknown audio mode: {value!r}")


def resolve_mode(mode: str, has_sink: bool, has_source: bool) -> str:
    """Degrade ``mode`` to what the available devices can provide."""
    if mode == "both":
        if has_sink and has_source:
            return "both"
        mode = "desktop" if has_sink else "mic"
    if mode == "desktop":
        return "desktop" if has_sink else "none"
    if mode == "mic":
        return "mic" if has_source else "none"
    return "none"


def monitor_of(sink: str) -> str:
    return f"{sink}.monitor"


def null_sink_args() -> list[str]:
    return [
        "load-module", "module-null-sink", f"sink_name={MIX_SINK}",
        f"sink_properties=device.description={MIX_DESCRIPTION}",
    ]


def loopback_args(source: str) -> list[str]:
    return [
        "load-module", "module-loopback", f"source={source}", f"sink={MIX_SINK}",
        f"latency_msec={LOOPBACK_LATENCY_MS}",
    ]


def unload_args(module_id: str) -> list[str]:
    return ["unload-module", module_id]


def parse_module_id(stdout: str) -> str:
    text = stdout.strip()
    if not text.isdigit():
        raise AudioError(f"unexpected load-module output: {text!r}")
    return text


def read_module_ids(path: Path) -> list[str]:
    try:
        text = path.read_text()
    except OSError:
        return []
    return [line.strip() for line in text.splitlines() if line.strip().isdigit()]


def _pactl(args: list[str]) -> str:
    return run_pactl(args)  # looked up at call time so tests can patch it


def unload_recorded(path: Path, runner: Runner = _pactl) -> list[str]:
    """Unload every module id recorded in ``path`` (newest first); delete the file.

    Returns the ids that were unloaded. Ids that no longer exist are skipped
    with a warning, so a stale file never blocks a new recording.
    """
    ids = read_module_ids(path)
    unloaded = []
    for module_id in reversed(ids):
        try:
            runner(unload_args(module_id))
            unloaded.append(module_id)
        except AudioError as exc:
            log.warning("could not unload audio module %s: %s", module_id, exc)
    path.unlink(missing_ok=True)
    return unloaded


# --- mixer -----------------------------------------------------------------


@dataclass
class AudioSetup:
    mode: str
    device: str | None
    module_ids: list[str] = field(default_factory=list)


class AudioMixer:
    """Resolves the capture device for a mode and owns any modules it loads."""

    def __init__(self, record_path: Path, runner: Runner = _pactl) -> None:
        self._record_path = record_path
        self._run = runner
        self._loaded: list[str] = []

    def _default(self, kind: str) -> str | None:
        try:
            name = self._run([f"get-default-{kind}"]).strip()
        except AudioError as exc:
            log.warning("no default %s: %s", kind, exc)
            return None
        return name or None

    def setup(self, mode: str) -> AudioSetup:
        if mode == "none":
            return AudioSetup("none", None)
        sink = self._default("sink") if mode in ("both", "desktop") else None
        source = self._default("source") if mode in ("both", "mic") else None
        effective = resolve_mode(mode, sink is not None, source is not None)
        if effective != mode:
            log.warning("audio mode %s unavailable; using %s", mode, effective)
        if effective == "both":
            assert sink is not None and source is not None
            try:
                self._load_mix(sink, source)
                return AudioSetup("both", monitor_of(MIX_SINK), list(self._loaded))
            except AudioError as exc:
                log.warning("could not create the audio mix (%s); recording desktop audio only", exc)
                self.teardown()
                effective = "desktop"
        if effective == "desktop":
            assert sink is not None
            return AudioSetup("desktop", monitor_of(sink))
        if effective == "mic":
            return AudioSetup("mic", source)
        return AudioSetup("none", None)

    def _load_mix(self, sink: str, source: str) -> None:
        self._record_path.parent.mkdir(parents=True, exist_ok=True)
        self._record_path.touch()
        for args in (null_sink_args(), loopback_args(monitor_of(sink)), loopback_args(source)):
            module_id = parse_module_id(self._run(args))
            self._loaded.append(module_id)
            with self._record_path.open("a") as handle:
                handle.write(f"{module_id}\n")

    def teardown(self) -> None:
        """Unload loaded modules in reverse order and delete the record file."""
        if self._record_path.exists():
            unload_recorded(self._record_path, self._run)
        else:
            for module_id in reversed(self._loaded):
                try:
                    self._run(unload_args(module_id))
                except AudioError as exc:
                    log.warning("could not unload audio module %s: %s", module_id, exc)
        self._loaded.clear()
