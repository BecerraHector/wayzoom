"""Damped spring integrator (DESIGN.md §1) and motion presets (DESIGN.md §2)."""

from __future__ import annotations

from dataclasses import dataclass, field

from wayzoom.domain.timeline import DT, Point


@dataclass(frozen=True)
class SpringParams:
    tension: float
    friction: float
    mass: float

    def regulated(self) -> SpringParams:
        """Stability guard: if ``friction / mass > 100`` use ``mass = friction * 0.01``."""
        mass = self.mass if self.mass > 0 else 1.0
        if self.friction / mass > 100:
            return SpringParams(self.tension, self.friction, self.friction * 0.01)
        return self


PRESETS: dict[str, SpringParams] = {
    "slow": SpringParams(170.0, 50.0, 3.0),
    "mellow": SpringParams(470.0, 70.0, 3.0),
    "quick": SpringParams(340.0, 60.0, 3.0),
    "rapid": SpringParams(530.0, 40.0, 1.0),
}

DEFAULT_CURSOR_PRESET = "mellow"
DEFAULT_ZOOM_PRESET = "slow"


def step_scalar(
    value: float,
    velocity: float,
    target: float,
    params: SpringParams,
    dt: float = DT,
) -> tuple[float, float]:
    """One semi-implicit Euler step. Returns the new ``(value, velocity)``."""
    mass = params.mass if params.mass > 0 else 1.0
    accel = ((target - value) * params.tension + (-velocity) * params.friction) / mass
    velocity += accel * dt
    value += velocity * dt
    return value, velocity


@dataclass
class SpringValue:
    value: float
    velocity: float = 0.0
    params: SpringParams = field(default_factory=lambda: PRESETS[DEFAULT_ZOOM_PRESET])

    def step(self, target: float, dt: float = DT) -> float:
        self.value, self.velocity = step_scalar(
            self.value, self.velocity, target, self.params, dt
        )
        return self.value

    def snap(self, value: float) -> None:
        self.value = value
        self.velocity = 0.0


@dataclass
class SpringPoint:
    x: SpringValue
    y: SpringValue

    @classmethod
    def at(cls, point: Point, params: SpringParams) -> SpringPoint:
        return cls(SpringValue(point[0], params=params), SpringValue(point[1], params=params))

    @property
    def position(self) -> Point:
        return (self.x.value, self.y.value)

    @property
    def velocity(self) -> Point:
        return (self.x.velocity, self.y.velocity)

    def step(self, target: Point, dt: float = DT) -> Point:
        self.x.step(target[0], dt)
        self.y.step(target[1], dt)
        return self.position

    def snap(self, point: Point) -> None:
        self.x.snap(point[0])
        self.y.snap(point[1])
