import unittest

from wayzoom.domain.cursor_motion import spring_cursor
from wayzoom.domain.spring import PRESETS


class SpringCursorTest(unittest.TestCase):
    def test_starts_at_first_position(self) -> None:
        out = spring_cursor([(5.0, 5.0), (5.0, 5.0)])
        self.assertEqual(out[0], (5.0, 5.0))

    def test_lags_then_converges(self) -> None:
        points = [(0.0, 0.0)] + [(100.0, 50.0)] * 400
        out = spring_cursor(points, PRESETS["mellow"])
        self.assertLess(out[1][0], 100.0)
        self.assertAlmostEqual(out[-1][0], 100.0, places=3)
        self.assertAlmostEqual(out[-1][1], 50.0, places=3)

    def test_empty(self) -> None:
        self.assertEqual(spring_cursor([]), [])


if __name__ == "__main__":
    unittest.main()
