import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from wayzoom.app import recording_session
from wayzoom.app.recording_session import ClickReader, CsvLog


class ClickReaderTest(unittest.TestCase):
    def test_reads_and_timestamps_valid_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fifo = Path(tmp) / "clicks.fifo"
            os.mkfifo(fifo)
            read_fd = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
            keep_fd = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)
            sink = CsvLog(Path(tmp) / "clicks.csv", ["t_mono", "kind", "button"])
            reader = ClickReader(read_fd, sink)
            reader.start()
            with open(fifo, "w") as writer:
                writer.write("down 272\ngarbage\nup 272\n")
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                if (Path(tmp) / "clicks.csv").read_text().count("\n") >= 3:
                    break
                time.sleep(0.02)
            reader.stop()
            reader.join(timeout=1)
            sink.close()
            os.close(keep_fd)
            os.close(read_fd)
            lines = (Path(tmp) / "clicks.csv").read_text().splitlines()
            self.assertEqual(lines[0], "t_mono,kind,button")
            self.assertEqual([line.split(",")[1:] for line in lines[1:]], [["down", "272"], ["up", "272"]])

    def test_key_lines_go_to_key_log_without_key_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fifo = Path(tmp) / "clicks.fifo"
            os.mkfifo(fifo)
            read_fd = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
            keep_fd = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)
            clicks = CsvLog(Path(tmp) / "clicks.csv", ["t_mono", "kind", "button"])
            keys = CsvLog(Path(tmp) / "keys.csv", ["t_mono"])
            reader = ClickReader(read_fd, clicks, keys)
            reader.start()
            with open(fifo, "w") as writer:
                writer.write("key\nkey a\ndown 272\nkey\n")
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                if (Path(tmp) / "keys.csv").read_text().count("\n") >= 3:
                    break
                time.sleep(0.02)
            reader.stop()
            reader.join(timeout=1)
            clicks.close()
            keys.close()
            os.close(keep_fd)
            os.close(read_fd)
            key_lines = (Path(tmp) / "keys.csv").read_text().splitlines()
            self.assertEqual(key_lines[0], "t_mono")
            self.assertEqual(len(key_lines), 3)
            for line in key_lines[1:]:
                float(line)  # a bare timestamp, nothing else
            self.assertEqual((Path(tmp) / "clicks.csv").read_text().count("\n"), 2)

    def test_key_lines_ignored_without_key_log(self) -> None:
        self.assertIsNone(recording_session.parse_fifo_line("key a"))
        self.assertEqual(recording_session.parse_fifo_line("key"), ("key",))
        self.assertEqual(recording_session.parse_fifo_line("up 273"), ("up", "273"))
        self.assertIsNone(recording_session.parse_fifo_line("down x"))


class StatusTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        patcher = mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": self._tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)

    def test_idle(self) -> None:
        self.assertEqual(recording_session.status(), "idle")

    def test_stale_pidfile_is_reported(self) -> None:
        runtime = recording_session.runtime_dir()
        runtime.mkdir(parents=True)
        (runtime / recording_session.PIDFILE).write_text(str(os.getpid()))  # not a daemon
        self.assertIsNone(recording_session.daemon_pid())
        self.assertIn("stale", recording_session.status())

    def test_cleanup_unbinds_recorded_keys_only(self) -> None:
        runtime = recording_session.runtime_dir()
        runtime.mkdir(parents=True)
        (runtime / recording_session.BOUND_KEYS_FILE).write_text("a\nspace\n")
        with (
            mock.patch("wayzoom.adapters.hyprland.remove_click_binds"),
            mock.patch("wayzoom.adapters.hyprland.remove_key_binds") as remove_keys,
        ):
            messages = recording_session.cleanup()
        remove_keys.assert_called_once_with(["a", "space"])
        self.assertFalse((runtime / recording_session.BOUND_KEYS_FILE).exists())
        self.assertTrue(any("2 key binds" in m for m in messages))

    def test_cleanup_without_key_file_touches_no_keys(self) -> None:
        with (
            mock.patch("wayzoom.adapters.hyprland.remove_click_binds"),
            mock.patch("wayzoom.adapters.hyprland.remove_key_binds") as remove_keys,
        ):
            recording_session.cleanup()
        remove_keys.assert_not_called()

    def test_cleanup_unloads_recorded_audio_modules(self) -> None:
        runtime = recording_session.runtime_dir()
        runtime.mkdir(parents=True)
        (runtime / recording_session.AUDIO_MODULES_FILE).write_text("11\n12\n13\n")
        calls: list[list[str]] = []
        with (
            mock.patch("wayzoom.adapters.hyprland.remove_click_binds"),
            mock.patch("wayzoom.adapters.audio_mixer.run_pactl", side_effect=lambda a: calls.append(a) or ""),
        ):
            messages = recording_session.cleanup()
        self.assertEqual(calls, [["unload-module", "13"], ["unload-module", "12"], ["unload-module", "11"]])
        self.assertFalse((runtime / recording_session.AUDIO_MODULES_FILE).exists())
        self.assertTrue(any("3 audio modules" in m for m in messages))

    def test_cleanup_without_audio_file_runs_no_pactl(self) -> None:
        with (
            mock.patch("wayzoom.adapters.hyprland.remove_click_binds"),
            mock.patch("wayzoom.adapters.audio_mixer.run_pactl") as pactl,
        ):
            recording_session.cleanup()
        pactl.assert_not_called()

    def test_stale_audio_modules_are_unloaded_before_setup(self) -> None:
        runtime = recording_session.runtime_dir()
        runtime.mkdir(parents=True)
        (runtime / recording_session.AUDIO_MODULES_FILE).write_text("21\n22\n")
        calls: list[list[str]] = []

        def pactl(args: list[str]) -> str:
            calls.append(args)
            return {"get-default-sink": "spk\n", "get-default-source": "mic\n"}.get(args[0], "")

        with mock.patch("wayzoom.adapters.audio_mixer.run_pactl", side_effect=pactl):
            mixer, setup = recording_session._setup_audio("desktop")
        self.assertEqual(calls[:2], [["unload-module", "22"], ["unload-module", "21"]])
        self.assertEqual((setup.mode, setup.device), ("desktop", "spk.monitor"))
        mixer.teardown()

    def test_stop_without_daemon_fails(self) -> None:
        with self.assertRaises(recording_session.SessionError):
            recording_session.stop()


if __name__ == "__main__":
    unittest.main()


class EventFilterTest(unittest.TestCase):
    def test_drops_screencast_events_emitted_by_the_recording_itself(self):
        self.assertFalse(recording_session.is_logged_event("screencast>>1,0"))
        self.assertFalse(recording_session.is_logged_event("screencastv2>>1,0,eDP-1"))

    def test_keeps_window_and_workspace_events(self):
        self.assertTrue(recording_session.is_logged_event("activewindow>>kitty,zsh"))
        self.assertTrue(recording_session.is_logged_event("workspacev2>>2,2"))
