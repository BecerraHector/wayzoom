import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from wayzoom.adapters.ffmpeg_renderer import filter_graph, render_command
from wayzoom.adapters.hyprland import (
    TYPING_KEYS,
    Monitor,
    bind_lua,
    bindable_keys,
    click_bind_statements,
    click_command,
    install_key_binds,
    key_bind_lua,
    key_command,
    key_unbind_lua,
    lua_string,
    parse_monitors,
    remove_key_binds,
    unbind_lua,
)
from wayzoom.adapters.screen_recorder import CaptureTarget, parse_geometry, recorder_command


class LuaTest(unittest.TestCase):
    def test_lua_string_escapes(self) -> None:
        self.assertEqual(lua_string('a"b\\c\nd'), '"a\\"b\\\\c\\nd"')

    def test_bind_and_unbind(self) -> None:
        self.assertEqual(
            bind_lua(272, "echo hi", release=True),
            'hl.bind("mouse:272", hl.dsp.exec_cmd("echo hi"), { non_consuming = true, release = true })',
        )
        self.assertEqual(unbind_lua(273), 'hl.unbind("mouse:273")')

    def test_three_binds(self) -> None:
        statements = click_bind_statements(Path("/run/user/1000/wayzoom/clicks.fifo"))
        self.assertEqual(len(statements), 3)
        self.assertIn("release = true", statements[1])
        self.assertIn("mouse:273", statements[2])

    def test_click_command_appends_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "with space.txt"
            subprocess.run(["sh", "-c", click_command(target, "up", 272)], check=True)
            subprocess.run(["sh", "-c", click_command(target, "down", 273)], check=True)
            self.assertEqual(target.read_text(), "up 272\ndown 273\n")

    def test_click_command_rejects_unknown_kind(self) -> None:
        with self.assertRaises(ValueError):
            click_command(Path("/tmp/x"), "drag; rm -rf /", 272)


class KeyBindTest(unittest.TestCase):
    def test_key_set(self) -> None:
        self.assertEqual(len(TYPING_KEYS), 26 + 10 + 5)
        for key in ("a", "z", "0", "9", "space", "ntilde", "comma", "period", "minus"):
            self.assertIn(key, TYPING_KEYS)

    def test_key_bind_lua(self) -> None:
        self.assertEqual(
            key_bind_lua("space", "echo hi"),
            'hl.bind("space", hl.dsp.exec_cmd("echo hi"), { non_consuming = true })',
        )
        self.assertEqual(key_unbind_lua("ntilde"), 'hl.unbind("ntilde")')

    def test_key_bind_lua_rejects_unknown_key(self) -> None:
        with self.assertRaises(ValueError):
            key_bind_lua('a") os.execute("x', "echo hi")

    def test_key_command_writes_no_key_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "with space.txt"
            subprocess.run(["sh", "-c", key_command(target)], check=True)
            subprocess.run(["sh", "-c", key_command(target)], check=True)
            self.assertEqual(target.read_text(), "key\nkey\n")

    def test_guard_skips_keys_with_unmodified_binds(self) -> None:
        binds = [
            {"key": "A", "modmask": 0, "submap": "resize"},
            {"key": "space", "modmask": 64, "submap": ""},
            {"key": "F9", "modmask": 0, "submap": ""},
            {"key": "comma", "modmask": 0, "submap": ""},
            {"key": "", "keycode": 38, "modmask": 0, "submap": ""},
        ]
        bindable, skipped = bindable_keys(binds)
        self.assertEqual(skipped, ["a", "comma"])
        self.assertIn("space", bindable)
        self.assertNotIn("a", bindable)
        self.assertEqual(len(bindable), len(TYPING_KEYS) - 2)

    def test_install_key_binds_respects_guard(self) -> None:
        binds = [{"key": "b", "modmask": 0, "submap": ""}]
        fifo = Path("/run/user/1000/wayzoom/clicks.fifo")
        with (
            mock.patch("wayzoom.adapters.hyprland.request_json", return_value=binds) as query,
            mock.patch("wayzoom.adapters.hyprland.eval_lua") as evaluate,
            self.assertLogs("wayzoom.adapters.hyprland", level="WARNING") as logs,
        ):
            bound = install_key_binds(fifo)
        query.assert_called_once_with("binds")
        self.assertNotIn("b", bound)
        self.assertEqual(len(bound), len(TYPING_KEYS) - 1)
        self.assertEqual(evaluate.call_count, len(bound))
        self.assertIn('hl.bind("a",', evaluate.call_args_list[0].args[0])
        self.assertIn("b", logs.output[0])

    def test_install_key_binds_reports_progress_before_binding(self) -> None:
        recorded: list[list[str]] = []
        with (
            mock.patch("wayzoom.adapters.hyprland.request_json", return_value=[]),
            mock.patch("wayzoom.adapters.hyprland.eval_lua") as evaluate,
        ):
            evaluate.side_effect = lambda _code: self.assertEqual(len(recorded), 1)
            install_key_binds(Path("/f"), on_planned=recorded.append)
        self.assertEqual(recorded[0], list(TYPING_KEYS))

    def test_remove_key_binds(self) -> None:
        with mock.patch("wayzoom.adapters.hyprland.eval_lua") as evaluate:
            remove_key_binds(["a", "space"])
        self.assertEqual(
            [c.args[0] for c in evaluate.call_args_list],
            ['hl.unbind("a")', 'hl.unbind("space")'],
        )

    def test_remove_key_binds_ignores_unknown_names(self) -> None:
        with (
            mock.patch("wayzoom.adapters.hyprland.eval_lua") as evaluate,
            self.assertLogs("wayzoom.adapters.hyprland", level="WARNING"),
        ):
            remove_key_binds(["a", "SUPER"])
        self.assertEqual(evaluate.call_count, 1)


class MonitorTest(unittest.TestCase):
    def test_logical_rect_with_scale(self) -> None:
        monitor = parse_monitors([
            {"name": "eDP-1", "width": 1920, "height": 1200, "x": 0, "y": 0,
             "scale": 1.5, "transform": 0, "focused": True},
        ])[0]
        self.assertEqual(monitor.logical_rect(), (0.0, 0.0, 1280.0, 800.0))

    def test_rotated_monitor_swaps_axes(self) -> None:
        monitor = Monitor("DP-1", 1920, 1080, 1280, 0, 1.0, 1, False)
        self.assertEqual(monitor.logical_rect(), (1280.0, 0.0, 1080.0, 1920.0))


class RecorderTest(unittest.TestCase):
    def test_parse_geometry(self) -> None:
        self.assertEqual(parse_geometry("10,-20 300x200"), (10.0, -20.0, 300.0, 200.0))
        with self.assertRaises(ValueError):
            parse_geometry("garbage")
        with self.assertRaises(ValueError):
            parse_geometry("0,0 0x10")

    def test_output_command(self) -> None:
        command = recorder_command(
            CaptureTarget(output="eDP-1"), Path("/s/raw.mkv"), audio_device="wayzoom_mix.monitor"
        )
        self.assertEqual(command[:4], ["wf-recorder", "-y", "-o", "eDP-1"])
        self.assertIn("preset=ultrafast", command)
        self.assertEqual(command[-1], "--audio=wayzoom_mix.monitor")

    def test_region_command(self) -> None:
        command = recorder_command(CaptureTarget(geometry="1,2 3x4"), Path("/s/raw.mkv"), audio_device=None)
        self.assertEqual(command[2:4], ["-g", "1,2 3x4"])
        self.assertFalse(any(part.startswith("--audio") for part in command))


class FfmpegTest(unittest.TestCase):
    def test_filter_graph_quotes_expressions(self) -> None:
        graph = filter_graph(1920, 1200, "if(lt(t,1),0,5)", "0", "1920", "1200")
        self.assertTrue(graph.startswith("setpts=PTS-STARTPTS,libplacebo=w=1920:h=1200"))
        self.assertIn("crop_x='if(lt(t,1),0,5)'", graph)
        self.assertTrue(graph.endswith(":format=yuv420p"))

    def test_render_command_audio(self) -> None:
        with_audio = render_command(Path("in.mkv"), Path("f.txt"), Path("o.mp4"), True)
        self.assertIn("-/vf", with_audio)
        self.assertIn("aac", with_audio)
        self.assertEqual(with_audio[-2:], ["-y", "o.mp4"])
        self.assertIn("-an", render_command(Path("in.mkv"), Path("f.txt"), Path("o.mp4"), False))


if __name__ == "__main__":
    unittest.main()
