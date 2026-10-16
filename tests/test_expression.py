import math
import random
import unittest

from tests.ffexpr import compile_expression, evaluate
from wayzoom.domain.expression import (
    format_number,
    piecewise_linear_expression,
    series_expression,
    simplify_rdp,
)


class FormatNumberTest(unittest.TestCase):
    def test_formats(self) -> None:
        self.assertEqual(format_number(1.0), "1")
        self.assertEqual(format_number(0.12345), "0.1235")
        self.assertEqual(format_number(-0.00001), "0")
        self.assertEqual(format_number(-2.5), "-2.5")


class SimplifyTest(unittest.TestCase):
    def test_collinear_points_collapse(self) -> None:
        points = [(float(i), 2.0 * i) for i in range(100)]
        self.assertEqual(simplify_rdp(points, 0.05), [points[0], points[-1]])

    def test_keeps_corner(self) -> None:
        points = [(0.0, 0.0), (1.0, 1.0), (2.0, 2.0), (3.0, 0.0)]
        self.assertEqual(simplify_rdp(points, 0.05), [(0.0, 0.0), (2.0, 2.0), (3.0, 0.0)])

    def test_large_input_does_not_recurse(self) -> None:
        points = [(i * 0.016, math.sin(i * 0.01) * 100) for i in range(20000)]
        self.assertGreater(len(simplify_rdp(points, 0.05)), 2)


class ExpressionTest(unittest.TestCase):
    def test_constant(self) -> None:
        expr = piecewise_linear_expression([(0.0, 5.0), (1.0, 5.0)])
        self.assertEqual(evaluate(expr, 0.5), 5.0)

    def test_single_point(self) -> None:
        self.assertEqual(piecewise_linear_expression([(0.0, 3.0)]), "3")

    def test_clamps_outside_range(self) -> None:
        expr = piecewise_linear_expression([(1.0, 0.0), (2.0, 10.0)])
        self.assertAlmostEqual(evaluate(expr, 0.0), 0.0)
        self.assertAlmostEqual(evaluate(expr, 1.5), 5.0)
        self.assertAlmostEqual(evaluate(expr, 9.0), 10.0)

    def test_negative_values(self) -> None:
        expr = piecewise_linear_expression([(0.0, -1.0), (1.0, -3.0), (2.0, 4.0)])
        self.assertAlmostEqual(evaluate(expr, 0.5), -2.0)
        self.assertAlmostEqual(evaluate(expr, 1.5), 0.5)

    def test_series_matches_within_epsilon(self) -> None:
        rng = random.Random(7)
        times = [i * 0.016 for i in range(2000)]
        value = 500.0
        values = []
        for _ in times:
            value += rng.uniform(-3, 3)
            values.append(value)
        epsilon = 0.05
        fn = compile_expression(series_expression(times, values, epsilon))
        for t, v in zip(times, values):
            self.assertLessEqual(abs(fn(t) - v), epsilon + 1e-6)
        for t in (times[0] + 0.008, times[500] + 0.004):
            self.assertTrue(math.isfinite(fn(t)))

    def test_smooth_series_is_compact(self) -> None:
        times = [i * 0.016 for i in range(1000)]
        values = [960.0 if t < 3 else 480.0 for t in times]
        expr = series_expression(times, values, 0.05)
        self.assertLess(len(expr), 200)
        self.assertAlmostEqual(evaluate(expr, 1.0), 960.0)
        self.assertAlmostEqual(evaluate(expr, 10.0), 480.0)


if __name__ == "__main__":
    unittest.main()
