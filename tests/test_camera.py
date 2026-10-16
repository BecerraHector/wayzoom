import unittest

from wayzoom.domain.camera import (
    CropRect,
    anchor_targets,
    crop_rects,
    get_anchor_point,
    scale_targets,
    spring_anchor,
    spring_scale,
)
from wayzoom.domain.timeline import frame_count
from wayzoom.domain.zoom_planner import ZoomSegment

W, H = 1920.0, 1200.0


class GetAnchorPointTest(unittest.TestCase):
    def test_identity_when_not_zoomed(self) -> None:
        self.assertEqual(get_anchor_point(1.0, (0.3, 0.7)), (0.3, 0.7))

    def test_centre_maps_to_centre(self) -> None:
        ax, ay = get_anchor_point(2.0, (0.5, 0.5))
        self.assertAlmostEqual(ax, 0.5)
        self.assertAlmostEqual(ay, 0.5)

    def test_visible_centre_is_preserved_inside_bounds(self) -> None:
        s = 2.0
        ax, _ = get_anchor_point(s, (0.4, 0.5))
        left = ax * (1.0 - 1.0 / s)
        self.assertAlmostEqual(left + 0.5 / s, 0.4)

    def test_clamped_at_edges(self) -> None:
        self.assertEqual(get_anchor_point(2.0, (0.01, 0.99)), (0.0, 1.0))


class ScaleTargetsTest(unittest.TestCase):
    def test_targets_for_one_segment(self) -> None:
        n = frame_count(6.0)
        targets = scale_targets([ZoomSegment(1.18, 2.766, 2.0)], n)
        self.assertEqual(targets, [(74, 1.0), (173, 2.0), (n - 1, 1.0)])

    def test_segment_at_zero_starts_zoomed(self) -> None:
        targets = scale_targets([ZoomSegment(0.0, 1.0, 2.0)], 200)
        self.assertEqual(targets[0], (62, 2.0))


class SpringScaleTest(unittest.TestCase):
    def test_no_segments_is_identity(self) -> None:
        self.assertEqual(spring_scale([], 10), [1.0] * 10)

    def test_zooms_in_and_back_out(self) -> None:
        n = frame_count(8.0)
        scale = spring_scale([ZoomSegment(1.0, 3.0, 2.0)], n)
        self.assertAlmostEqual(scale[0], 1.0)
        self.assertGreater(max(scale), 1.8)
        self.assertLess(abs(scale[-1] - 1.0), 0.05)
        self.assertTrue(all(value >= 0.0 for value in scale))


class AnchorTest(unittest.TestCase):
    def test_no_segments_centres(self) -> None:
        anchors = spring_anchor([], [(0.0, 0.0)] * 3, [1.0] * 3, W, H)
        self.assertEqual(anchors, [(0.5, 0.5)] * 3)

    def test_first_cluster_is_instant_when_zoomed_out(self) -> None:
        n = frame_count(6.0)
        positions = [(1700.0, 1100.0)] * n
        segments = [ZoomSegment(1.18, 2.766, 2.0)]
        scale = spring_scale(segments, n)
        targets = anchor_targets(segments, positions, scale, W, H)
        self.assertEqual(len(targets), 1)
        self.assertTrue(targets[0].instant)
        self.assertEqual(targets[0].index, 172)  # closes at e - 1
        self.assertEqual(targets[0].point, (1.0, 1.0))
        anchors = spring_anchor(segments, positions, scale, W, H)
        self.assertEqual(anchors[0], (1.0, 1.0))
        self.assertEqual(anchors[-1], (1.0, 1.0))

    def test_cluster_breaks_when_cursor_leaves_half_viewport(self) -> None:
        n = frame_count(6.0)
        positions = [(200.0, 200.0)] * 100 + [(1700.0, 1000.0)] * (n - 100)
        segments = [ZoomSegment(1.0, 3.0, 2.0)]
        scale = spring_scale(segments, n)
        targets = anchor_targets(segments, positions, scale, W, H)
        self.assertEqual([t.index for t in targets], [100, 187])
        self.assertTrue(targets[0].instant)
        self.assertFalse(targets[1].instant)

    def test_previous_target_index_moves_to_next_segment_start(self) -> None:
        n = frame_count(12.0)
        positions = [(960.0, 600.0)] * n
        segments = [ZoomSegment(1.0, 2.0, 2.0), ZoomSegment(6.0, 7.0, 2.0)]
        scale = spring_scale(segments, n)
        targets = anchor_targets(segments, positions, scale, W, H)
        self.assertEqual(targets[0].index, 375)
        self.assertEqual(len(targets), 2)

    def test_spring_moves_toward_non_instant_target(self) -> None:
        n = frame_count(6.0)
        positions = [(200.0, 200.0)] * 100 + [(1700.0, 1000.0)] * (n - 100)
        segments = [ZoomSegment(1.0, 3.0, 2.0)]
        scale = spring_scale(segments, n)
        targets = anchor_targets(segments, positions, scale, W, H)
        anchors = spring_anchor(segments, positions, scale, W, H)
        self.assertEqual(anchors[0], targets[0].point)
        self.assertGreater(anchors[150][0], anchors[0][0])
        self.assertLessEqual(anchors[150][0], 1.0)

    def test_cluster_closes_when_caret_source_switches(self) -> None:
        n = frame_count(6.0)
        positions = [(1000.0, 600.0)] * n
        flags = [False] * 100 + [True] * (n - 100)
        segments = [ZoomSegment(1.0, 3.0, 2.0)]
        scale = spring_scale(segments, n)
        without = anchor_targets(segments, positions, scale, W, H)
        self.assertEqual([t.index for t in without], [187])
        targets = anchor_targets(segments, positions, scale, W, H, flags)
        self.assertEqual([t.index for t in targets], [99, 187])
        self.assertTrue(targets[0].instant)
        self.assertFalse(targets[1].instant)

    def test_caret_switch_after_drift_close_is_not_emitted_twice(self) -> None:
        n = frame_count(6.0)
        positions = [(200.0, 200.0)] * 100 + [(1700.0, 1000.0)] * (n - 100)
        flags = [False] * 101 + [True] * (n - 101)
        segments = [ZoomSegment(1.0, 3.0, 2.0)]
        scale = spring_scale(segments, n)
        targets = anchor_targets(segments, positions, scale, W, H, flags)
        self.assertEqual([t.index for t in targets], [100, 187])

    def test_spring_anchor_accepts_caret_flags(self) -> None:
        n = frame_count(6.0)
        positions = [(1700.0, 1100.0)] * n
        segments = [ZoomSegment(1.18, 2.766, 2.0)]
        scale = spring_scale(segments, n)
        anchors = spring_anchor(segments, positions, scale, W, H, uses_caret=[True] * n)
        self.assertEqual(anchors[-1], (1.0, 1.0))


class CropRectsTest(unittest.TestCase):
    def test_crop_stays_inside_frame(self) -> None:
        rects = crop_rects(
            [1.0, 2.0, 0.8, 2.0],
            [(0.5, 0.5), (1.0, 0.0), (0.2, 0.2), (1.3, -0.2)],
            W,
            H,
        )
        self.assertEqual(rects[0], CropRect(0.0, 0.0, W, H))
        self.assertEqual(rects[1], CropRect(960.0, 0.0, 960.0, 600.0))
        self.assertEqual(rects[2], CropRect(0.0, 0.0, W, H))
        self.assertEqual(rects[3], CropRect(960.0, 0.0, 960.0, 600.0))


if __name__ == "__main__":
    unittest.main()
