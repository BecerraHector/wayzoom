import unittest

from wayzoom.domain.timeline import (
    DT,
    CursorSample,
    click_frames,
    frame_count,
    frame_index,
    frame_times,
    resample_cursor,
)


class TimelineTest(unittest.TestCase):
    def test_dt(self) -> None:
        self.assertEqual(DT, 0.016)

    def test_frame_count_is_floor_plus_one(self) -> None:
        self.assertEqual(frame_count(6.0), 376)
        self.assertEqual(frame_count(0.0), 1)
        self.assertEqual(frame_count(0.017), 2)

    def test_frame_times(self) -> None:
        times = frame_times(4)
        self.assertEqual(len(times), 4)
        self.assertAlmostEqual(times[3], 0.048)

    def test_frame_index_rounds_and_clamps(self) -> None:
        self.assertEqual(frame_index(0.0, 10), 0)
        self.assertEqual(frame_index(0.025, 10), 2)
        self.assertEqual(frame_index(-1.0, 10), 0)
        self.assertEqual(frame_index(100.0, 10), 9)


class ResampleTest(unittest.TestCase):
    def test_linear_interpolation_and_clamping(self) -> None:
        samples = [CursorSample(0.016, 0.0, 0.0), CursorSample(0.048, 32.0, 64.0)]
        points = resample_cursor(samples, 5)
        self.assertEqual(points[0], (0.0, 0.0))  # clamped before first sample
        self.assertEqual(points[1], (0.0, 0.0))
        self.assertAlmostEqual(points[2][0], 16.0)
        self.assertAlmostEqual(points[2][1], 32.0)
        self.assertEqual(points[3], (32.0, 64.0))
        self.assertEqual(points[4], (32.0, 64.0))  # clamped after last sample

    def test_unsorted_samples_are_sorted(self) -> None:
        samples = [CursorSample(0.032, 10.0, 0.0), CursorSample(0.0, 0.0, 0.0)]
        points = resample_cursor(samples, 3)
        self.assertAlmostEqual(points[1][0], 5.0)

    def test_no_samples_uses_fallback(self) -> None:
        self.assertEqual(resample_cursor([], 2, fallback=(5.0, 6.0)), [(5.0, 6.0)] * 2)


class ClickFramesTest(unittest.TestCase):
    def test_nearest_index(self) -> None:
        self.assertEqual(click_frames([0.0, 0.030, 99.0], 10), {0, 2, 9})


if __name__ == "__main__":
    unittest.main()
