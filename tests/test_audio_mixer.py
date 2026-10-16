import tempfile
import unittest
from pathlib import Path

from wayzoom.adapters import audio_mixer
from wayzoom.adapters.audio_mixer import AudioError, AudioMixer


class FakePactl:
    """Records pactl calls; answers defaults and hands out module ids."""

    def __init__(self, sink="speakers", source="mic0", fail_on=None) -> None:
        self.sink = sink
        self.source = source
        self.fail_on = fail_on  # substring of a load-module call that fails
        self.calls: list[list[str]] = []
        self._next_id = 536870912

    def __call__(self, args: list[str]) -> str:
        self.calls.append(args)
        if args == ["get-default-sink"]:
            if self.sink is None:
                raise AudioError("no sink")
            return f"{self.sink}\n"
        if args == ["get-default-source"]:
            if self.source is None:
                raise AudioError("no source")
            return f"{self.source}\n"
        if args[0] == "load-module":
            if self.fail_on and any(self.fail_on in a for a in args):
                raise AudioError("load failed")
            self._next_id += 1
            return f"{self._next_id}\n"
        if args[0] == "unload-module":
            return ""
        raise AssertionError(f"unexpected call {args}")

    def loads(self) -> list[list[str]]:
        return [c for c in self.calls if c[0] == "load-module"]

    def unloads(self) -> list[str]:
        return [c[1] for c in self.calls if c[0] == "unload-module"]


class ModeResolutionTest(unittest.TestCase):
    def test_both_degrades_to_available_side(self) -> None:
        self.assertEqual(audio_mixer.resolve_mode("both", True, True), "both")
        self.assertEqual(audio_mixer.resolve_mode("both", True, False), "desktop")
        self.assertEqual(audio_mixer.resolve_mode("both", False, True), "mic")
        self.assertEqual(audio_mixer.resolve_mode("both", False, False), "none")

    def test_single_modes(self) -> None:
        self.assertEqual(audio_mixer.resolve_mode("desktop", True, False), "desktop")
        self.assertEqual(audio_mixer.resolve_mode("desktop", False, True), "none")
        self.assertEqual(audio_mixer.resolve_mode("mic", False, True), "mic")
        self.assertEqual(audio_mixer.resolve_mode("mic", True, False), "none")
        self.assertEqual(audio_mixer.resolve_mode("none", True, True), "none")

    def test_normalize_legacy_flag(self) -> None:
        self.assertEqual(audio_mixer.normalize_mode(True), "mic")
        self.assertEqual(audio_mixer.normalize_mode(False), "none")
        self.assertEqual(audio_mixer.normalize_mode(None), "none")
        self.assertEqual(audio_mixer.normalize_mode("desktop"), "desktop")
        with self.assertRaises(ValueError):
            audio_mixer.normalize_mode("loud")


class CommandTest(unittest.TestCase):
    def test_module_commands(self) -> None:
        self.assertEqual(
            audio_mixer.null_sink_args(),
            ["load-module", "module-null-sink", "sink_name=wayzoom_mix",
             "sink_properties=device.description=WayZoom-mix"],
        )
        self.assertEqual(
            audio_mixer.loopback_args("speakers.monitor"),
            ["load-module", "module-loopback", "source=speakers.monitor", "sink=wayzoom_mix",
             "latency_msec=20"],
        )
        self.assertEqual(audio_mixer.unload_args("42"), ["unload-module", "42"])

    def test_parse_module_id(self) -> None:
        self.assertEqual(audio_mixer.parse_module_id("536870913\n"), "536870913")
        with self.assertRaises(AudioError):
            audio_mixer.parse_module_id("Failure: Module initialization failed")


class MixerTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.record = Path(self._tmp.name) / "wayzoom" / "audio_modules"

    def test_desktop_and_mic_load_no_modules(self) -> None:
        pactl = FakePactl()
        desktop = AudioMixer(self.record, pactl).setup("desktop")
        mic = AudioMixer(self.record, pactl).setup("mic")
        self.assertEqual((desktop.mode, desktop.device), ("desktop", "speakers.monitor"))
        self.assertEqual((mic.mode, mic.device), ("mic", "mic0"))
        self.assertEqual(pactl.loads(), [])
        self.assertFalse(self.record.exists())

    def test_none_touches_nothing(self) -> None:
        pactl = FakePactl()
        result = AudioMixer(self.record, pactl).setup("none")
        self.assertEqual((result.mode, result.device), ("none", None))
        self.assertEqual(pactl.calls, [])

    def test_both_loads_mix_and_records_ids(self) -> None:
        pactl = FakePactl()
        mixer = AudioMixer(self.record, pactl)
        result = mixer.setup("both")
        self.assertEqual((result.mode, result.device), ("both", "wayzoom_mix.monitor"))
        loads = pactl.loads()
        self.assertEqual(loads[0], audio_mixer.null_sink_args())
        self.assertEqual(loads[1], audio_mixer.loopback_args("speakers.monitor"))
        self.assertEqual(loads[2], audio_mixer.loopback_args("mic0"))
        self.assertEqual(audio_mixer.read_module_ids(self.record), result.module_ids)
        self.assertEqual(len(result.module_ids), 3)

    def test_teardown_unloads_in_reverse_and_deletes_record(self) -> None:
        pactl = FakePactl()
        mixer = AudioMixer(self.record, pactl)
        ids = mixer.setup("both").module_ids
        mixer.teardown()
        self.assertEqual(pactl.unloads(), list(reversed(ids)))
        self.assertFalse(self.record.exists())
        mixer.teardown()  # idempotent
        self.assertEqual(len(pactl.unloads()), 3)

    def test_failed_loopback_unloads_partial_mix_and_falls_back_to_desktop(self) -> None:
        pactl = FakePactl(fail_on="source=mic0")
        mixer = AudioMixer(self.record, pactl)
        result = mixer.setup("both")
        self.assertEqual((result.mode, result.device), ("desktop", "speakers.monitor"))
        self.assertEqual(len(pactl.unloads()), 2)  # loopback, then null sink
        self.assertFalse(self.record.exists())

    def test_missing_source_degrades_both_to_desktop(self) -> None:
        pactl = FakePactl(source=None)
        result = AudioMixer(self.record, pactl).setup("both")
        self.assertEqual((result.mode, result.device), ("desktop", "speakers.monitor"))
        self.assertEqual(pactl.loads(), [])

    def test_no_pactl_degrades_to_none(self) -> None:
        def missing(_args: list[str]) -> str:
            raise AudioError("pactl is not installed")

        result = AudioMixer(self.record, missing).setup("both")
        self.assertEqual((result.mode, result.device), ("none", None))

    def test_unload_recorded_skips_bad_ids_and_deletes_file(self) -> None:
        self.record.parent.mkdir(parents=True)
        self.record.write_text("7\n8\ngarbage\n9\n")
        calls: list[str] = []

        def runner(args: list[str]) -> str:
            calls.append(args[1])
            if args[1] == "8":
                raise AudioError("no such module")
            return ""

        self.assertEqual(audio_mixer.unload_recorded(self.record, runner), ["9", "7"])
        self.assertEqual(calls, ["9", "8", "7"])
        self.assertFalse(self.record.exists())

    def test_unload_recorded_without_file_is_noop(self) -> None:
        self.assertEqual(audio_mixer.unload_recorded(self.record, FakePactl()), [])


if __name__ == "__main__":
    unittest.main()
