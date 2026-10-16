import contextlib
import io
import unittest

from wayzoom.cli import _start_request, build_parser


class ParserTest(unittest.TestCase):
    def test_render_options(self) -> None:
        args = build_parser().parse_args(
            ["render", "/s", "--zoom", "2.5", "--zoom-speed", "quick", "--plan"]
        )
        self.assertEqual(args.zoom, 2.5)
        self.assertEqual(args.zoom_speed, "quick")
        self.assertEqual(args.cursor_speed, "mellow")
        self.assertTrue(args.plan)

    def test_toggle_defaults(self) -> None:
        args = build_parser().parse_args(["toggle", "--region"])
        self.assertTrue(args.region)
        self.assertEqual(args.audio, "both")
        self.assertEqual(args.zoom_speed, "slow")

    def test_typing_zoom_flags(self) -> None:
        parser = build_parser()
        self.assertTrue(parser.parse_args(["start"]).typing_zoom)
        self.assertFalse(parser.parse_args(["toggle", "--no-typing-zoom"]).typing_zoom)
        self.assertIsNone(parser.parse_args(["render", "/s"]).typing_zoom)
        self.assertFalse(parser.parse_args(["render", "/s", "--no-typing-zoom"]).typing_zoom)

    def test_start_request_carries_typing_flag(self) -> None:
        args = build_parser().parse_args(["start", "--no-typing-zoom"])
        self.assertFalse(_start_request(args).typing_zoom)

    def test_audio_modes(self) -> None:
        parser = build_parser()
        self.assertEqual(parser.parse_args(["start"]).audio, "both")
        self.assertEqual(parser.parse_args(["start", "--audio", "mic"]).audio, "mic")
        self.assertEqual(parser.parse_args(["toggle", "--audio", "desktop"]).audio, "desktop")
        self.assertEqual(parser.parse_args(["toggle", "--no-audio"]).audio, "none")
        self.assertEqual(_start_request(parser.parse_args(["start", "--no-audio"])).audio, "none")
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            parser.parse_args(["start", "--audio", "loud"])
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            parser.parse_args(["start", "--audio", "mic", "--no-audio"])

    def test_rejects_unknown_preset(self) -> None:
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            build_parser().parse_args(["start", "--zoom-speed", "warp"])


if __name__ == "__main__":
    unittest.main()
