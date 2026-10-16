import json
import tempfile
import unittest
from pathlib import Path

from tests.ffexpr import compile_expression
from wayzoom.adapters.ffmpeg_renderer import VideoInfo
from wayzoom.app.render_session import (
    CaptureRect,
    RenderOptions,
    SessionData,
    align_keys,
    align_to_video,
    crop_expressions,
    default_output,
    load_session,
    plan_camera,
    plan_summary,
)

INFO = VideoInfo(width=1920, height=1200, duration=6.0, has_audio=False)


def _session() -> SessionData:
    session = SessionData(rect=CaptureRect(0, 0, 1280, 800), t_stop_mono=1006.0)
    for i in range(376):
        t = i * 0.016
        k = min(t / 1.4, 1.0)
        session.cursor.append((1000.0 + t, 200 + 950 * k, 200 + 520 * k))
    session.clicks.append((1001.5, "down", 272))
    return session


class AlignTest(unittest.TestCase):
    def test_time_and_pixel_mapping(self) -> None:
        session = SessionData(rect=CaptureRect(100, 50, 1280, 800), t_stop_mono=1006.0)
        session.cursor.append((1001.0, 740.0, 450.0))
        session.clicks.append((1002.0, "down", 272))
        samples, clicks = align_to_video(session, INFO)
        self.assertAlmostEqual(samples[0].t, 1.0)
        self.assertAlmostEqual(samples[0].x, 960.0)
        self.assertAlmostEqual(samples[0].y, 600.0)
        self.assertAlmostEqual(clicks[0].t, 2.0)


class PlanTest(unittest.TestCase):
    def test_single_click_plan(self) -> None:
        samples, clicks = align_to_video(_session(), INFO)
        plan = plan_camera(samples, clicks, INFO, RenderOptions())
        summary = plan_summary(plan)
        self.assertEqual(summary["segments"], [{"begin": 1.18, "end": 2.766, "zoom": 2.0}])
        self.assertEqual(len(plan.crops), 376)
        zoomed = plan.crops[156]  # t = 2.5 s
        self.assertLess(zoomed.w, 1100)
        self.assertGreater(zoomed.x, 800)  # camera sits on the bottom-right
        self.assertAlmostEqual(plan.crops[0].w, 1920.0)

    def test_expressions_reproduce_crops(self) -> None:
        samples, clicks = align_to_video(_session(), INFO)
        plan = plan_camera(samples, clicks, INFO, RenderOptions())
        expressions = crop_expressions(plan, 0.05)
        for axis, expr in zip("xywh", expressions, strict=True):
            fn = compile_expression(expr)
            for t, crop in zip(plan.times, plan.crops):
                self.assertLessEqual(abs(fn(t) - getattr(crop, axis)), 0.05 + 1e-6)

    def test_no_clicks_keeps_full_frame(self) -> None:
        session = _session()
        session.clicks.clear()
        samples, clicks = align_to_video(session, INFO)
        plan = plan_camera(samples, clicks, INFO, RenderOptions())
        self.assertEqual(plan.segments, [])
        self.assertEqual(crop_expressions(plan), ("0", "0", "1920", "1200"))


E2E_INFO = VideoInfo(width=1920, height=1200, duration=10.0, has_audio=False)


def _typing_session(typing_start: float) -> SessionData:
    """Click at 1.0 s on the bottom-right, then typing with the cursor far away."""
    session = SessionData(rect=CaptureRect(0, 0, 1280, 800), t_stop_mono=1010.0)
    for i in range(626):
        t = i * 0.016
        x, y = (1000.0, 600.0) if t < 3.0 else (40.0, 40.0)
        session.cursor.append((1000.0 + t, x, y))
    session.clicks.append((1001.0, "down", 272))
    session.keys.extend(1000.0 + typing_start + 0.15 * i for i in range(12))
    return session


class TypingPlanTest(unittest.TestCase):
    def _plan(self, typing_start: float, typing: bool = True):
        session = _typing_session(typing_start)
        samples, clicks = align_to_video(session, E2E_INFO)
        keys = align_keys(session, E2E_INFO)
        return plan_camera(samples, clicks, E2E_INFO, RenderOptions(), keys if typing else [])

    def _assert_targets_click(self, plan, t: float) -> None:
        crop = plan.crops[round(t / 0.016)]
        self.assertLess(crop.w, 1100)
        self.assertGreater(crop.x, 700)  # anchored on the click (bottom-right) ...
        self.assertGreater(crop.y, 400)  # ... not on the cursor at the top-left

    def test_typing_adds_its_own_segment_and_targets_last_click(self) -> None:
        plan = self._plan(6.0)
        self.assertEqual(plan_summary(plan)["typing_ranges"], [[6.0, 7.65]])
        self.assertEqual(len(plan.segments), 2)
        self.assertAlmostEqual(plan.segments[1].begin, 5.5)
        self.assertAlmostEqual(plan.segments[1].end, 7.65)
        self._assert_targets_click(plan, 6.8)

    def test_typing_close_to_click_merges_and_still_targets_click(self) -> None:
        plan = self._plan(5.0)
        self.assertEqual(plan_summary(plan)["typing_ranges"], [[5.0, 6.65]])
        self.assertEqual(len(plan.segments), 1)  # gap 4.5 - 2.266 <= 2.6 s
        self.assertAlmostEqual(plan.segments[0].begin, 0.68)
        self.assertAlmostEqual(plan.segments[0].end, 6.65)
        self._assert_targets_click(plan, 5.8)

    def test_no_typing_zoom_removes_typing_segment(self) -> None:
        plan = self._plan(6.0, typing=False)
        self.assertEqual(plan_summary(plan)["typing_ranges"], [])
        self.assertEqual(len(plan.segments), 1)
        self.assertLess(plan.segments[0].end, 3.0)

    def test_render_option_overrides_meta(self) -> None:
        self.assertFalse(RenderOptions(typing_zoom=False).typing_enabled(True))
        self.assertTrue(RenderOptions().typing_enabled(True))
        self.assertFalse(RenderOptions().typing_enabled(False))


class SessionFilesTest(unittest.TestCase):
    def test_load_session_skips_headers_and_bad_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "meta.json").write_text(json.dumps({
                "capture": {"x": 0, "y": 0, "width": 1280, "height": 800},
                "t_stop_mono": 10.0,
            }))
            (d / "cursor.csv").write_text("t_mono,x_log,y_log\n1.0,2.0,3.0\nbad\n")
            (d / "clicks.csv").write_text("t_mono,kind,button\n1.5,down,272\n")
            (d / "keys.csv").write_text("t_mono\n2.0\nnope\n2.5\n")
            with self.assertLogs("wayzoom.app.render_session", level="WARNING"):
                session = load_session(d)
            self.assertEqual(session.cursor, [(1.0, 2.0, 3.0)])
            self.assertEqual(session.clicks, [(1.5, "down", 272)])
            self.assertEqual(session.keys, [2.0, 2.5])
            self.assertTrue(session.typing_zoom)

    def test_meta_args_disable_typing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "meta.json").write_text(json.dumps({
                "capture": {"x": 0, "y": 0, "width": 1280, "height": 800},
                "t_stop_mono": 10.0,
                "args": {"typing_zoom": False},
            }))
            self.assertFalse(load_session(d).typing_zoom)

    def test_default_output(self) -> None:
        path = default_output(Path("/v/wayzoom/.sessions/20261015-120000"))
        self.assertEqual(path, Path("/v/wayzoom/wayzoom-20261015-120000.mp4"))


if __name__ == "__main__":
    unittest.main()
