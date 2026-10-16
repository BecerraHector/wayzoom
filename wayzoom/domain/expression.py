"""Compile a sampled series into a compact FFmpeg expression of ``t``.

The series is simplified with Ramer-Douglas-Peucker (iterative) and emitted as
a balanced binary tree of ``if(lt(t,T),LEFT,RIGHT)`` over linear segments.
"""

from __future__ import annotations

from collections.abc import Sequence

Sample = tuple[float, float]  # (time, value)

DEFAULT_EPSILON = 0.05
DECIMALS = 4
# Formatting with DECIMALS rounds each value, delta and time; the simplifier
# reserves this margin so the emitted expression stays within ``epsilon``.
ROUNDING_MARGIN = 1.5 * 10.0**-DECIMALS


def format_number(value: float, decimals: int = DECIMALS) -> str:
    text = f"{value:.{decimals}f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def _distance(p: Sample, a: Sample, b: Sample) -> float:
    """Vertical distance from ``p`` to the line through ``a`` and ``b``.

    The value deviation at a given time is what matters for rendering, so the
    error is measured along the value axis rather than perpendicular.
    """
    span = b[0] - a[0]
    if span == 0:
        return abs(p[1] - a[1])
    return abs(p[1] - (a[1] + (b[1] - a[1]) * (p[0] - a[0]) / span))


def simplify_rdp(points: Sequence[Sample], epsilon: float = DEFAULT_EPSILON) -> list[Sample]:
    """Ramer-Douglas-Peucker without recursion."""
    count = len(points)
    if count <= 2:
        return list(points)
    keep = [False] * count
    keep[0] = keep[-1] = True
    stack = [(0, count - 1)]
    while stack:
        lo, hi = stack.pop()
        best, best_index = -1.0, -1
        for i in range(lo + 1, hi):
            d = _distance(points[i], points[lo], points[hi])
            if d > best:
                best, best_index = d, i
        if best > epsilon:
            keep[best_index] = True
            stack.append((lo, best_index))
            stack.append((best_index, hi))
    return [p for p, k in zip(points, keep) if k]


def _leaf(a: Sample, b: Sample) -> str:
    v0, v1 = format_number(a[1]), format_number(b[1])
    if v0 == v1:
        return v0
    t0 = format_number(a[0])
    dv = format_number(b[1] - a[1])
    dt = format_number(b[0] - a[0])
    return f"({v0}+({dv})*(t-{t0})/{dt})"


def _tree(points: Sequence[Sample], lo: int, hi: int) -> str:
    """Expression for segments ``lo .. hi-1`` (segment k joins points k and k+1).

    Recursion depth is logarithmic in the number of segments.
    """
    if hi - lo == 1:
        return _leaf(points[lo], points[lo + 1])
    mid = (lo + hi) // 2
    split = format_number(points[mid][0])
    return f"if(lt(t,{split}),{_tree(points, lo, mid)},{_tree(points, mid, hi)})"


def piecewise_linear_expression(points: Sequence[Sample]) -> str:
    """Piecewise linear interpolation of ``points``, clamped outside their range."""
    if not points:
        raise ValueError("at least one point is required")
    if len(points) == 1:
        return format_number(points[0][1])
    first, last = points[0], points[-1]
    if all(format_number(p[1]) == format_number(first[1]) for p in points):
        return format_number(first[1])
    body = _tree(points, 0, len(points) - 1)
    return (
        f"if(lt(t,{format_number(first[0])}),{format_number(first[1])},"
        f"if(gte(t,{format_number(last[0])}),{format_number(last[1])},{body}))"
    )


def series_expression(
    times: Sequence[float],
    values: Sequence[float],
    epsilon: float = DEFAULT_EPSILON,
) -> str:
    """Simplify the series and compile it into an expression of ``t``.

    The result deviates from every sample by at most ``epsilon`` (plus
    negligible float error) when ``epsilon`` exceeds the rounding margin.
    """
    samples = list(zip(times, values, strict=True))
    return piecewise_linear_expression(simplify_rdp(samples, max(epsilon - ROUNDING_MARGIN, 0.0)))
