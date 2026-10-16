"""Recording lifecycle: start a detached daemon, capture telemetry, stop, render.

Session layout (``${XDG_VIDEOS_DIR:-~/Videos}/wayzoom/.sessions/<ts>/``):

- ``raw.mkv``      wf-recorder output
- ``cursor.csv``   ``t_mono,x_log,y_log`` cursor samples (logical coordinates)
- ``clicks.csv``   ``t_mono,kind,button`` (kind is ``down`` or ``up``)
- ``keys.csv``     ``t_mono`` of each unmodified text keystroke; key names are
                   never recorded (empty with ``--no-typing-zoom``)
- ``events.log``   Hyprland socket2 events (minus the per-frame ``screencast``
                   noise caused by the recording itself); recorded for
                   diagnostics only, the planner does not use them
- ``meta.json``    capture rect (logical), monitor scale, timestamps, arguments
- ``daemon.log``   daemon log; ``wf-recorder.log`` and ``ffmpeg.log`` tool output
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import logging
import os
import select
import signal
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import wayzoom
from wayzoom.adapters import audio_mixer, hyprland, notifier
from wayzoom.adapters.audio_mixer import AudioMixer, AudioSetup
from wayzoom.adapters.screen_recorder import CaptureTarget, ScreenRecorder, parse_geometry, select_region
from wayzoom.app import render_session
from wayzoom.app.render_session import RenderOptions

log = logging.getLogger(__name__)

PIDFILE = "recorder.pid"
STATEFILE = "state"
FIFO = "clicks.fifo"
BOUND_KEYS_FILE = "bound_keys"
AUDIO_MODULES_FILE = "audio_modules"
REQUEST_FILE = "request.json"
START_TIMEOUT = 5.0


class SessionError(RuntimeError):
    pass


# --- locations -------------------------------------------------------------


def videos_root() -> Path:
    base = os.environ.get("XDG_VIDEOS_DIR") or str(Path.home() / "Videos")
    return Path(base).expanduser() / "wayzoom"


def sessions_root() -> Path:
    return videos_root() / ".sessions"


def runtime_dir() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/wayzoom-{os.getuid()}"
    return Path(base) / "wayzoom"


# --- daemon bookkeeping ----------------------------------------------------


def _read_pid() -> int | None:
    try:
        return int((runtime_dir() / PIDFILE).read_text().strip())
    except (OSError, ValueError):
        return None


def _is_daemon(pid: int) -> bool:
    """True when ``pid`` is alive and is a WayZoom daemon (guards against PID reuse)."""
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return False
    return b"wayzoom" in cmdline and b"_daemon" in cmdline


def daemon_pid() -> int | None:
    pid = _read_pid()
    return pid if pid is not None and _is_daemon(pid) else None


def _read_state() -> dict[str, object]:
    try:
        data = json.loads((runtime_dir() / STATEFILE).read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_state(state: str, session_dir: Path) -> None:
    path = runtime_dir() / STATEFILE
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"pid": os.getpid(), "state": state, "session": str(session_dir)}))
    tmp.replace(path)


# --- commands --------------------------------------------------------------


@dataclass(frozen=True)
class StartRequest:
    region: bool = False
    audio: str = audio_mixer.DEFAULT_MODE
    zoom: float = 2.0
    zoom_speed: str = "slow"
    cursor_speed: str = "mellow"
    typing_zoom: bool = True


def _capture_for(request: StartRequest) -> tuple[CaptureTarget, dict[str, object], float]:
    if request.region:
        geometry = select_region()
        x, y, w, h = parse_geometry(geometry)
        monitor = hyprland.monitor_containing(x, y) or hyprland.focused_monitor()
        target = CaptureTarget(geometry=geometry)
        rect = {"x": x, "y": y, "width": w, "height": h, "output": None, "geometry": geometry}
    else:
        monitor = hyprland.focused_monitor()
        x, y, w, h = monitor.logical_rect()
        target = CaptureTarget(output=monitor.name)
        rect = {"x": x, "y": y, "width": w, "height": h, "output": monitor.name, "geometry": None}
    return target, rect, monitor.scale


def start(request: StartRequest) -> Path:
    if daemon_pid() is not None:
        raise SessionError("a recording is already running (use `wayzoom stop`)")
    _, rect, scale = _capture_for(request)
    session_dir = sessions_root() / dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    session_dir.mkdir(parents=True, exist_ok=False)
    (session_dir / REQUEST_FILE).write_text(
        json.dumps({"capture": rect, "monitor_scale": scale, "args": asdict(request)}, indent=2)
    )
    runtime_dir().mkdir(parents=True, exist_ok=True)
    package_root = str(Path(wayzoom.__file__).resolve().parent.parent)
    env = dict(os.environ)
    env["PYTHONPATH"] = package_root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    with open(session_dir / "daemon.log", "ab") as log_file:
        process = subprocess.Popen(
            [sys.executable, "-m", "wayzoom", "_daemon", str(session_dir)],
            cwd=session_dir,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    deadline = time.monotonic() + START_TIMEOUT
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise SessionError(f"recorder daemon exited early; see {session_dir / 'daemon.log'}")
        state = _read_state()
        if state.get("pid") == process.pid and state.get("state") == "recording":
            break
        time.sleep(0.05)
    else:
        log.warning("daemon did not confirm recording within %.0f s", START_TIMEOUT)
    notifier.notify("Recording", "Run `wayzoom toggle` again to stop.")
    return session_dir


def stop() -> str:
    pid = daemon_pid()
    if pid is None:
        raise SessionError("no recording is running")
    if _read_state().get("state") == "rendering":
        return "rendering"
    os.kill(pid, signal.SIGINT)
    return "stopping"


def toggle(request: StartRequest) -> str:
    if daemon_pid() is None:
        return f"recording to {start(request)}"
    result = stop()
    if result == "rendering":
        notifier.notify("Still rendering", "The previous recording is still being rendered.")
        return "previous recording is still rendering"
    return "stopping; the video is rendered in the background"


def status() -> str:
    pid = daemon_pid()
    if pid is not None:
        state = _read_state()
        return f"{state.get('state', 'running')} (pid {pid}, session {state.get('session', '?')})"
    if (runtime_dir() / PIDFILE).exists():
        return "stale pidfile (run `wayzoom cleanup`)"
    return "idle"


def cleanup() -> list[str]:
    if daemon_pid() is not None:
        raise SessionError("the daemon is running; use `wayzoom stop` instead")
    messages = []
    try:
        hyprland.remove_click_binds()
        messages.append("removed mouse:272/273 click binds")
    except (RuntimeError, hyprland.HyprlandUnavailable) as exc:
        messages.append(f"could not remove click binds: {exc}")
    keys = _read_bound_keys()
    if keys is not None:
        try:
            hyprland.remove_key_binds(keys)
            (runtime_dir() / BOUND_KEYS_FILE).unlink(missing_ok=True)
            messages.append(f"removed {len(keys)} key binds")
        except (RuntimeError, hyprland.HyprlandUnavailable) as exc:
            messages.append(f"could not remove key binds: {exc}")
    audio_record = runtime_dir() / AUDIO_MODULES_FILE
    if audio_record.exists():
        unloaded = audio_mixer.unload_recorded(audio_record)
        messages.append(f"unloaded {len(unloaded)} audio modules")
    for name in (PIDFILE, STATEFILE, FIFO):
        path = runtime_dir() / name
        if path.exists() or path.is_symlink():
            path.unlink()
            messages.append(f"removed {path}")
    return messages


# --- daemon ----------------------------------------------------------------


class CsvLog:
    """Thread-safe append-only CSV writer, flushed per row."""

    def __init__(self, path: Path, header: list[str]) -> None:
        self._handle = path.open("w", newline="", buffering=1)
        self._writer = csv.writer(self._handle)
        self._lock = threading.Lock()
        self._writer.writerow(header)

    def write(self, *row: object) -> None:
        with self._lock:
            self._writer.writerow(row)

    def close(self) -> None:
        with self._lock:
            self._handle.close()


def parse_fifo_line(line: str) -> tuple[str, ...] | None:
    """Validate one FIFO line: ``down|up <button>`` or a bare ``key``."""
    parts = tuple(line.split())
    if len(parts) == 2 and parts[0] in ("down", "up") and parts[1].isdigit():
        return parts
    if parts == ("key",):
        return parts
    return None


class ClickReader(threading.Thread):
    """Timestamps click and keystroke lines arriving on the FIFO."""

    def __init__(self, fd: int, sink: CsvLog, key_sink: CsvLog | None = None) -> None:
        super().__init__(name="click-reader", daemon=True)
        self._fd = fd
        self._sink = sink
        self._key_sink = key_sink
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        buffer = b""
        while not self._stop.is_set():
            ready, _, _ = select.select([self._fd], [], [], 0.1)
            if not ready:
                continue
            now = time.monotonic()
            try:
                chunk = os.read(self._fd, 4096)
            except BlockingIOError:
                continue
            buffer += chunk
            *lines, buffer = buffer.split(b"\n")
            for line in lines:
                parts = parse_fifo_line(line.decode(errors="replace"))
                if parts is None:
                    continue
                if parts == ("key",):
                    if self._key_sink is not None:
                        self._key_sink.write(f"{now:.6f}")
                else:
                    self._sink.write(f"{now:.6f}", parts[0], parts[1])


# --- key binds bookkeeping -------------------------------------------------


def _read_bound_keys() -> list[str] | None:
    """Keys a daemon bound, from the runtime file; ``None`` when absent."""
    try:
        text = (runtime_dir() / BOUND_KEYS_FILE).read_text()
    except OSError:
        return None
    return [line.strip() for line in text.splitlines() if line.strip()]


def _write_bound_keys(keys: list[str]) -> None:
    path = runtime_dir() / BOUND_KEYS_FILE
    tmp = path.with_suffix(".tmp")
    tmp.write_text("".join(f"{key}\n" for key in keys))
    tmp.replace(path)


def _install_key_binds(fifo: Path) -> None:
    stale = _read_bound_keys()
    if stale:  # left behind by a daemon that was killed hard
        log.warning("removing %d stale key binds", len(stale))
        hyprland.remove_key_binds(stale)
    bound = hyprland.install_key_binds(fifo, on_planned=_write_bound_keys)
    _write_bound_keys(bound)
    log.info("typing capture: %d keys bound", len(bound))


def _remove_key_binds() -> None:
    keys = _read_bound_keys()
    if keys is None:
        return
    hyprland.remove_key_binds(keys)
    (runtime_dir() / BOUND_KEYS_FILE).unlink(missing_ok=True)


# --- audio ------------------------------------------------------------------


def _setup_audio(mode: str) -> tuple[AudioMixer, AudioSetup]:
    """Unload modules a dead daemon left behind, then prepare ``mode``."""
    record = runtime_dir() / AUDIO_MODULES_FILE
    if record.exists():
        log.warning("unloading stale audio modules")
        audio_mixer.unload_recorded(record)
    mixer = AudioMixer(record)
    setup = mixer.setup(mode)
    log.info("audio: %s (%s)", setup.mode, setup.device or "no device")
    return mixer, setup


def _acquire_pidfile() -> bool:
    path = runtime_dir() / PIDFILE
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = _read_pid()
    if existing is not None and existing != os.getpid() and _is_daemon(existing):
        return False
    path.write_text(str(os.getpid()))
    return True


def _release_runtime_files() -> None:
    if _read_pid() == os.getpid():
        for name in (PIDFILE, STATEFILE):
            (runtime_dir() / name).unlink(missing_ok=True)


def run_daemon(session_dir: Path) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
        force=True,
    )
    if not _acquire_pidfile():
        log.error("another daemon is running")
        return 1
    stop_event = threading.Event()
    stop_time: list[float] = []

    def on_signal(signum: int, _frame: object) -> None:
        if not stop_event.is_set():
            stop_time.append(time.monotonic())
            log.info("received signal %d; stopping", signum)
            stop_event.set()

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    try:
        return _record_and_render(session_dir, stop_event, stop_time)
    except Exception as exc:  # last resort: always tell the user
        log.exception("daemon failed")
        notifier.notify("WayZoom failed", str(exc), urgency="critical")
        return 1
    finally:
        _release_runtime_files()


# Our own screen capture makes Hyprland emit these on every frame.
_NOISY_EVENTS = frozenset({"screencast", "screencastv2"})


def is_logged_event(line: str) -> bool:
    """Whether a socket2 event line is worth keeping in ``events.log``."""
    return line.partition(">>")[0] not in _NOISY_EVENTS


def _record(session_dir: Path, request: dict, stop_event: threading.Event, stop_time: list[float]) -> dict:
    args = request["args"]
    capture = request["capture"]
    target = CaptureTarget(output=capture.get("output"), geometry=capture.get("geometry"))
    fifo = runtime_dir() / FIFO
    fifo.unlink(missing_ok=True)
    os.mkfifo(fifo, 0o600)
    read_fd = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
    keep_fd = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)  # avoids EOF between writers
    cursor_log = CsvLog(session_dir / render_session.CURSOR_FILE, ["t_mono", "x_log", "y_log"])
    clicks_log = CsvLog(session_dir / render_session.CLICKS_FILE, ["t_mono", "kind", "button"])
    keys_log = CsvLog(session_dir / render_session.KEYS_FILE, ["t_mono"])
    typing = bool(args.get("typing_zoom", True))
    events_file = (session_dir / "events.log").open("w", buffering=1)
    events_lock = threading.Lock()

    def on_event(t: float, line: str) -> None:
        if not is_logged_event(line):
            return
        with events_lock:
            events_file.write(f"{t:.6f} {line}\n")

    threads = [
        hyprland.CursorPoller(lambda t, x, y: cursor_log.write(f"{t:.6f}", x, y)),
        hyprland.EventListener(on_event),
        ClickReader(read_fd, clicks_log, keys_log if typing else None),
    ]
    recorder: ScreenRecorder | None = None
    mixer: AudioMixer | None = None
    t_start = time.monotonic()
    try:
        mode = audio_mixer.normalize_mode(args.get("audio"))
        try:
            mixer, audio = _setup_audio(mode)
        except (OSError, audio_mixer.AudioError) as exc:
            log.error("audio capture unavailable: %s", exc)
            audio = AudioSetup("none", None)
        if mode != "none" and audio.mode != mode:
            notifier.notify("WayZoom: audio limited", f"Recording audio: {audio.mode}")
        args["audio"] = mode
        args["audio_mode"] = audio.mode
        args["audio_device"] = audio.device
        recorder = ScreenRecorder(
            target, session_dir / render_session.RAW_VIDEO, audio.device,
            session_dir / "wf-recorder.log",
        )
        try:
            hyprland.install_click_binds(fifo)
        except (RuntimeError, hyprland.HyprlandUnavailable) as exc:
            log.error("click capture unavailable: %s", exc)
            notifier.notify("WayZoom: clicks not captured", str(exc), urgency="critical")
        if typing:
            try:
                _install_key_binds(fifo)
            except (RuntimeError, OSError, ValueError, hyprland.HyprlandUnavailable) as exc:
                log.error("typing capture unavailable: %s", exc)
                notifier.notify("WayZoom: typing not captured", str(exc))
        for thread in threads:
            thread.start()
        recorder.start()
        _write_state("recording", session_dir)
        while not stop_event.wait(0.25):
            if recorder.poll() is not None:
                log.error("wf-recorder exited unexpectedly with %s", recorder.poll())
                break
        t_stop = stop_time[0] if stop_time else time.monotonic()
        log.info("wf-recorder exited with %s", recorder.stop())
    finally:
        try:
            hyprland.remove_click_binds()
        except (RuntimeError, hyprland.HyprlandUnavailable) as exc:
            log.error("failed to remove click binds (run `wayzoom cleanup`): %s", exc)
        try:
            _remove_key_binds()
        except (RuntimeError, hyprland.HyprlandUnavailable) as exc:
            log.error("failed to remove key binds (run `wayzoom cleanup`): %s", exc)
        if recorder is not None:
            recorder.stop()
        if mixer is not None:
            try:
                mixer.teardown()
            except OSError as exc:
                log.error("failed to remove audio modules (run `wayzoom cleanup`): %s", exc)
        for thread in threads:
            thread.stop()  # type: ignore[attr-defined]
        for thread in threads:
            if thread.is_alive():
                thread.join(timeout=1.0)
        os.close(keep_fd)
        os.close(read_fd)
        fifo.unlink(missing_ok=True)
        cursor_log.close()
        clicks_log.close()
        keys_log.close()
        with events_lock:
            events_file.close()
    return {
        "version": 1,
        "capture": capture,
        "monitor_scale": request.get("monitor_scale", 1.0),
        "t_start_mono": t_start,
        "t_stop_mono": t_stop,
        "args": args,
    }


def _record_and_render(session_dir: Path, stop_event: threading.Event, stop_time: list[float]) -> int:
    request = json.loads((session_dir / REQUEST_FILE).read_text())
    meta = _record(session_dir, request, stop_event, stop_time)
    (session_dir / render_session.META_FILE).write_text(json.dumps(meta, indent=2))
    raw = session_dir / render_session.RAW_VIDEO
    if not raw.exists() or raw.stat().st_size == 0:
        notifier.notify("WayZoom failed", "No video was recorded.", urgency="critical")
        return 1
    _write_state("rendering", session_dir)
    notifier.notify("Rendering…", str(session_dir.name))
    args = meta["args"]
    options = RenderOptions(
        zoom=float(args.get("zoom", 2.0)),
        zoom_speed=str(args.get("zoom_speed", "slow")),
        cursor_speed=str(args.get("cursor_speed", "mellow")),
        typing_zoom=bool(args.get("typing_zoom", True)),
    )
    try:
        output = render_session.render_session(
            session_dir, render_session.default_output(session_dir), options
        )
    except Exception as exc:
        log.exception("render failed")
        notifier.notify("WayZoom render failed", str(exc), urgency="critical")
        return 1
    log.info("saved %s", output)
    notifier.notify("Saved", str(output))
    return 0
