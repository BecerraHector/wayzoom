import unittest

from wayzoom.domain.zoom_planner import (
    KeyStroke,
    ZoomSegment,
    key_typing_ranges,
    plan_zoom_segments,
    typing_ranges,
)


class PlanZoomSegmentsTest(unittest.TestCase):
    def test_single_click(self) -> None:
        segments = plan_zoom_segments([1.5], duration=6.0)
        self.assertEqual(len(segments), 1)
        self.assertAlmostEqual(segments[0].begin, 1.18)
        self.assertAlmostEqual(segments[0].end, 2.766)
        self.assertEqual(segments[0].zoom, 2.0)

    def test_close_clicks_merge(self) -> None:
        segments = plan_zoom_segments([1.0, 4.0], duration=20.0)
        self.assertEqual(len(segments), 1)
        self.assertAlmostEqual(segments[0].begin, 0.68)
        self.assertAlmostEqual(segments[0].end, 5.266)

    def test_far_clicks_stay_separate(self) -> None:
        segments = plan_zoom_segments([1.0, 6.0], duration=20.0)
        self.assertEqual(len(segments), 2)

    def test_clamped_to_video_bounds(self) -> None:
        segments = plan_zoom_segments([0.1, 8.5], duration=10.0)
        self.assertAlmostEqual(segments[0].begin, 0.0)
        self.assertAlmostEqual(segments[-1].end, 9.0)

    def test_empty_interval_dropped(self) -> None:
        self.assertEqual(plan_zoom_segments([9.9], duration=10.0, zoom=2.0), [])

    def test_non_positive_zoom_falls_back(self) -> None:
        segments = plan_zoom_segments([1.5], duration=6.0, zoom=0.0)
        self.assertEqual(segments[0].zoom, 2.0)

    def test_custom_zoom_and_typing_ranges(self) -> None:
        segments = plan_zoom_segments([], duration=30.0, zoom=3.0, typing=[(10.0, 12.0)])
        self.assertEqual(segments, [ZoomSegment(9.5, 12.0, 3.0)])

    def test_typing_interval_merges_with_click_interval(self) -> None:
        segments = plan_zoom_segments([3.0], duration=10.0, typing=[(5.0, 6.0)])
        self.assertEqual(len(segments), 1)
        self.assertAlmostEqual(segments[0].begin, 2.68)
        self.assertAlmostEqual(segments[0].end, 6.0)

    def test_typing_interval_clamped_to_video(self) -> None:
        segments = plan_zoom_segments([], duration=8.0, typing=[(0.2, 7.5)])
        self.assertEqual(segments, [ZoomSegment(0.0, 7.0, 2.0)])


class KeyTypingRangesTest(unittest.TestCase):
    def test_key_times_become_typing_range_in_seconds(self) -> None:
        times = [5.0 + 0.15 * i for i in range(12)]
        ranges = key_typing_ranges(times)
        self.assertEqual(len(ranges), 1)
        self.assertAlmostEqual(ranges[0][0], 5.0)
        self.assertAlmostEqual(ranges[0][1], 6.65)

    def test_too_few_keys_yield_nothing(self) -> None:
        self.assertEqual(key_typing_ranges([1.0, 1.1, 1.2]), [])
        self.assertEqual(key_typing_ranges([]), [])


def _keys(times_ms, char="a", modifiers=frozenset()):
    return [KeyStroke(t, char, frozenset(modifiers)) for t in times_ms]


class TypingRangesTest(unittest.TestCase):
    def test_five_strokes_qualify(self) -> None:
        self.assertEqual(typing_ranges(_keys([0, 100, 200, 300, 400])), [(0, 400)])

    def test_four_short_strokes_do_not_qualify(self) -> None:
        self.assertEqual(typing_ranges(_keys([0, 100, 200, 300])), [])

    def test_long_duration_qualifies(self) -> None:
        self.assertEqual(typing_ranges(_keys([0, 1000, 1600])), [(0, 1600)])

    def test_idle_gap_splits(self) -> None:
        keys = _keys([0, 100, 200, 300, 400, 5000, 5100, 5200, 5300, 5400])
        self.assertEqual(typing_ranges(keys), [(0, 400), (5000, 5400)])

    def test_modifiers_and_empty_chars_are_ignored(self) -> None:
        keys = _keys([0, 100, 200, 300], modifiers={"control"}) + _keys([400], char="")
        self.assertEqual(typing_ranges(keys), [])

    def test_first_qualifying_absorbs_preceding_fragment(self) -> None:
        keys = _keys([0, 100]) + _keys([2000, 2100, 2200, 2300, 2400])
        self.assertEqual(typing_ranges(keys), [(0, 2400)])

    def test_absorb_gap_limit(self) -> None:
        keys = _keys([0, 100]) + _keys([2700, 2800, 2900, 3000, 3100])
        self.assertEqual(typing_ranges(keys), [(2700, 3100)])


if __name__ == "__main__":
    unittest.main()
