import unittest

from wayzoom.domain.caret import caret_point, effective_positions
from wayzoom.domain.timeline import frame_count, frame_times


class CaretPointTest(unittest.TestCase):
    def test_last_click_at_or_before_range_start(self) -> None:
        clicks = [(1.0, (100.0, 100.0)), (4.0, (1500.0, 900.0)), (6.0, (10.0, 10.0))]
        cursor = [(0.0, 0.0)] * 500
        self.assertEqual(caret_point(5.0, clicks, cursor), (1500.0, 900.0))
        self.assertEqual(caret_point(4.0, clicks, cursor), (1500.0, 900.0))

    def test_falls_back_to_cursor_at_range_start(self) -> None:
        cursor = [(float(i), 2.0 * i) for i in range(500)]
        clicks = [(6.0, (10.0, 10.0))]
        self.assertEqual(caret_point(1.6, clicks, cursor), (100.0, 200.0))  # frame 100
        self.assertEqual(caret_point(1.6, [], cursor), (100.0, 200.0))


class EffectivePositionsTest(unittest.TestCase):
    def test_caret_replaces_cursor_inside_lead_window(self) -> None:
        n = frame_count(10.0)
        times = frame_times(n)
        cursor = [(50.0, 60.0)] * n
        clicks = [(1.0, (1500.0, 900.0))]
        positions, flags = effective_positions(cursor, times, [(5.0, 6.65)], clicks)
        for t, point, flag in zip(times, positions, flags, strict=True):
            inside = 4.5 - 1e-9 <= t <= 6.65 + 1e-9
            self.assertEqual(flag, inside, t)
            self.assertEqual(point, (1500.0, 900.0) if inside else (50.0, 60.0))

    def test_window_clamped_to_video(self) -> None:
        n = frame_count(2.0)
        times = frame_times(n)
        cursor = [(5.0, 5.0)] * n
        positions, flags = effective_positions(cursor, times, [(0.2, 9.0)], [])
        self.assertTrue(all(flags))
        self.assertEqual(positions[0], (5.0, 5.0))

    def test_no_ranges_keeps_cursor(self) -> None:
        cursor = [(1.0, 2.0)] * 10
        positions, flags = effective_positions(cursor, frame_times(10), [], [(0.0, (9.0, 9.0))])
        self.assertEqual(positions, cursor)
        self.assertEqual(flags, [False] * 10)


if __name__ == "__main__":
    unittest.main()
