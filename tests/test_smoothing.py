import unittest

from wayzoom.domain.smoothing import jitter_score, smooth_path
from wayzoom.domain.timeline import frame_times


class JitterScoreTest(unittest.TestCase):
    def test_straight_line_scores_zero(self) -> None:
        points = [(0.0, 0.0), (1.0, 0.0), (2.0, 0.0)]
        self.assertAlmostEqual(jitter_score(points, frame_times(3), 1), 0.0)

    def test_zero_length_segment_scores_zero(self) -> None:
        points = [(0.0, 0.0), (0.0, 0.0), (2.0, 0.0)]
        self.assertEqual(jitter_score(points, frame_times(3), 1), 0.0)

    def test_right_angle_score(self) -> None:
        points = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)]
        score = jitter_score(points, frame_times(3), 1)
        expected = 90.0 / 0.032 * 8.0 / 9.0
        self.assertAlmostEqual(score, expected, places=6)


class SmoothPathTest(unittest.TestCase):
    def test_straight_path_is_unchanged(self) -> None:
        points = [(float(i), 2.0 * i) for i in range(20)]
        self.assertEqual(smooth_path(points, frame_times(20), set()), points)

    def test_jitter_spike_is_interpolated(self) -> None:
        points = [(float(i), 0.0) for i in range(20)]
        points[10] = (10.0, 3.0)
        smoothed = smooth_path(points, frame_times(20), set())
        self.assertAlmostEqual(smoothed[10][0], 10.0)
        self.assertAlmostEqual(smoothed[10][1], 0.0)
        self.assertEqual(smoothed[0], points[0])
        self.assertEqual(smoothed[-1], points[-1])

    def test_frames_near_clicks_are_anchors(self) -> None:
        points = [(float(i), 0.0) for i in range(20)]
        points[10] = (10.0, 3.0)
        smoothed = smooth_path(points, frame_times(20), {13})
        self.assertEqual(smoothed[10], (10.0, 3.0))

    def test_high_intensity_keeps_mild_turns(self) -> None:
        points = [(float(i), 0.0) for i in range(10)] + [
            (9.0, float(j)) for j in range(1, 10)
        ]
        low = smooth_path(points, frame_times(len(points)), set(), intensity=100)
        high = smooth_path(points, frame_times(len(points)), set(), intensity=5000)
        self.assertNotEqual(low[9], points[9])
        self.assertNotEqual(high[9], points[9])  # 2000 cap still smooths a 90 degree turn
        self.assertEqual(len(low), len(points))


if __name__ == "__main__":
    unittest.main()
