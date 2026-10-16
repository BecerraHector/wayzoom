import unittest

from wayzoom.domain.spring import (
    PRESETS,
    SpringParams,
    SpringPoint,
    SpringValue,
    step_scalar,
)


class StepScalarTest(unittest.TestCase):
    def test_single_step_matches_semi_implicit_euler(self) -> None:
        params = SpringParams(tension=170.0, friction=50.0, mass=3.0)
        x, v = step_scalar(0.0, 0.0, 10.0, params, dt=0.016)
        a = (10.0 * 170.0) / 3.0
        expected_v = a * 0.016
        self.assertAlmostEqual(v, expected_v)
        self.assertAlmostEqual(x, expected_v * 0.016)

    def test_non_positive_mass_is_treated_as_one(self) -> None:
        params = SpringParams(tension=100.0, friction=0.0, mass=0.0)
        _, v = step_scalar(0.0, 0.0, 1.0, params, dt=0.01)
        self.assertAlmostEqual(v, 1.0)

    def test_converges_to_target(self) -> None:
        spring = SpringValue(0.0, params=PRESETS["slow"])
        for _ in range(2000):
            spring.step(5.0)
        self.assertAlmostEqual(spring.value, 5.0, places=4)
        self.assertAlmostEqual(spring.velocity, 0.0, places=4)


class RegulatedTest(unittest.TestCase):
    def test_regulated_raises_mass_when_ratio_exceeds_100(self) -> None:
        params = SpringParams(tension=100.0, friction=500.0, mass=1.0).regulated()
        self.assertAlmostEqual(params.mass, 5.0)

    def test_regulated_keeps_stable_params(self) -> None:
        params = PRESETS["slow"]
        self.assertEqual(params.regulated(), params)


class PresetsTest(unittest.TestCase):
    def test_preset_values(self) -> None:
        self.assertEqual(PRESETS["slow"], SpringParams(170, 50, 3))
        self.assertEqual(PRESETS["mellow"], SpringParams(470, 70, 3))
        self.assertEqual(PRESETS["quick"], SpringParams(340, 60, 3))
        self.assertEqual(PRESETS["rapid"], SpringParams(530, 40, 1))


class SpringPointTest(unittest.TestCase):
    def test_axes_are_independent(self) -> None:
        point = SpringPoint.at((0.0, 0.0), PRESETS["mellow"])
        point.step((10.0, 0.0))
        x, y = point.position
        self.assertGreater(x, 0.0)
        self.assertEqual(y, 0.0)

    def test_snap_resets_velocity(self) -> None:
        point = SpringPoint.at((0.0, 0.0), PRESETS["mellow"])
        point.step((10.0, 10.0))
        point.snap((3.0, 4.0))
        self.assertEqual(point.position, (3.0, 4.0))
        self.assertEqual(point.velocity, (0.0, 0.0))


if __name__ == "__main__":
    unittest.main()
