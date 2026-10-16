"""Command line interface."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from wayzoom import __version__
from wayzoom.adapters.ffmpeg_renderer import RenderError
from wayzoom.adapters.hyprland import HyprlandUnavailable
from wayzoom.app import recording_session, render_session
from wayzoom.app.recording_session import SessionError, StartRequest
from wayzoom.app.render_session import RenderOptions
from wayzoom.domain.spring import DEFAULT_CURSOR_PRESET, DEFAULT_ZOOM_PRESET, PRESETS
from wayzoom.domain.zoom_planner import DEFAULT_ZOOM

COMMANDS = "{toggle,start,stop,status,render,cleanup}"


def _add_motion_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--zoom", type=float, default=DEFAULT_ZOOM, help="zoom factor (default: 2.0)")
    parser.add_argument(
        "--zoom-speed", choices=sorted(PRESETS), default=DEFAULT_ZOOM_PRESET,
        help=f"camera spring preset (default: {DEFAULT_ZOOM_PRESET})",
    )
    parser.add_argument(
        "--cursor-speed", choices=sorted(PRESETS), default=DEFAULT_CURSOR_PRESET,
        help=f"cursor spring preset (default: {DEFAULT_CURSOR_PRESET})",
    )


def _add_capture_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--region", action="store_true", help="select a region with slurp")
    parser.add_argument("--audio", action="store_true", help="record default audio source")
    parser.add_argument(
        "--no-typing-zoom", dest="typing_zoom", action="store_false",
        help="do not capture keystroke timestamps (no key binds, no typing zoom)",
    )
    _add_motion_options(parser)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wayzoom",
        description="Cinematic auto-zoom screen recorder for Hyprland.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True, metavar=COMMANDS)
    _add_capture_options(sub.add_parser("toggle", help="start recording, or stop and render"))
    _add_capture_options(sub.add_parser("start", help="start recording in the background"))
    sub.add_parser("stop", help="stop recording and render")
    sub.add_parser("status", help="print the recorder state")
    sub.add_parser("cleanup", help="remove click/key binds and stale runtime files")
    render = sub.add_parser("render", help="render (or re-render) a recorded session")
    render.add_argument("session_dir", type=Path, metavar="SESSION_DIR")
    _add_motion_options(render)
    render.add_argument("--output", type=Path, help="output file (default: next to the sessions)")
    render.add_argument("--plan", action="store_true", help="print the zoom plan as JSON and exit")
    render.add_argument(
        "--no-typing-zoom", dest="typing_zoom", action="store_const", const=False, default=None,
        help="ignore recorded keystrokes (default: follow the recording's setting)",
    )
    daemon = sub.add_parser("_daemon")
    daemon.add_argument("session_dir", type=Path)
    return parser


def _start_request(args: argparse.Namespace) -> StartRequest:
    return StartRequest(
        region=args.region, audio=args.audio, zoom=args.zoom,
        zoom_speed=args.zoom_speed, cursor_speed=args.cursor_speed,
        typing_zoom=args.typing_zoom,
    )


def _render(args: argparse.Namespace) -> int:
    session_dir: Path = args.session_dir
    options = RenderOptions(
        zoom=args.zoom, zoom_speed=args.zoom_speed, cursor_speed=args.cursor_speed,
        typing_zoom=args.typing_zoom,
    )
    if args.plan:
        _, plan = render_session.prepare(session_dir, options)
        print(json.dumps(render_session.plan_summary(plan), indent=2))
        return 0
    output = args.output or render_session.default_output(session_dir)
    print(render_session.render_session(session_dir, output, options))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "_daemon":
        return recording_session.run_daemon(args.session_dir)
    logging.basicConfig(level=logging.WARNING, format="wayzoom: %(message)s")
    try:
        match args.command:
            case "toggle":
                print(recording_session.toggle(_start_request(args)))
            case "start":
                print(f"recording to {recording_session.start(_start_request(args))}")
            case "stop":
                result = recording_session.stop()
                print("previous recording is still rendering" if result == "rendering" else result)
            case "status":
                print(recording_session.status())
            case "cleanup":
                for message in recording_session.cleanup():
                    print(message)
            case "render":
                return _render(args)
    except (SessionError, RenderError, HyprlandUnavailable, FileNotFoundError, RuntimeError) as exc:
        print(f"wayzoom: {exc}", file=sys.stderr)
        return 1
    return 0
