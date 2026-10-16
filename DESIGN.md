# WayZoom design

This document specifies the motion model behind WayZoom. Code comments refer to
its sections as `DESIGN.md §N`. All simulation runs offline, after recording,
on a fixed timeline with step `dt = 0.016 s` (62.5 Hz).

## §1 Spring model

Every animated value (cursor position, zoom scale, camera anchor) follows a
damped spring integrated with semi-implicit Euler:

```
a  = ((target - x) * tension - v * friction) / mass      (mass <= 0 → 1)
v += a * dt
x += v * dt
```

Stability guard: when `friction / mass > 100`, use `mass = friction * 0.01`.

Targets are step functions: a list of `(frame_index, value)` pairs where each
value is the target for all frames up to and including its index; the next
pair takes over on the following frame.

## §2 Presets

| Preset | Tension | Friction | Mass | Character | Default for |
|---|---|---|---|---|---|
| `slow` | 170 | 50 | 3 | overdamped, no overshoot | camera (scale + anchor) |
| `mellow` | 470 | 70 | 3 | slightly underdamped | cursor |
| `quick` | 340 | 60 | 3 | slightly underdamped | — |
| `rapid` | 530 | 40 | 1 | underdamped, small overshoot | — |

## §3 Zoom planning

Inputs: click events (button down and up) and typing ranges (§4), in seconds
from the start of the video of duration `D`.

1. Each click at `c` proposes `[c - 0.32, c + 1.266]`.
2. Each typing range `[s, e]` proposes `[s - 0.5, e]`.
3. Clamp every interval to `[0, D - 1.0]` (no zoom during the last second);
   drop empty intervals.
4. Sort by start; merge an interval into the previous one when
   `start <= previous_end + 2.6`.
5. Each merged interval becomes a zoom segment with the configured zoom
   (default `2.0`).

## §4 Typing detection

1. Keep key-down events that produce text without modifiers.
2. Split them into fragments wherever two strokes are more than 1500 ms apart.
3. A fragment is a typing range when it lasts at least 1500 ms **or** has at
   least 5 strokes.
4. The first typing range absorbs an immediately preceding short fragment if
   the gap between them is at most 2500 ms.

## §5 Timeline

Cursor samples are linearly interpolated onto the grid `t_i = i * dt`,
`i = 0 … floor(D / dt)`, holding the first/last sample outside the recorded
span. A grid frame nearest to each click is flagged as a click frame.
`index(t) = clamp(round(t / dt), 0, N - 1)`.

## §6 Jitter smoothing

Small, fast direction changes (hand tremor) are removed before the cursor
spring:

- Anchor frames are never modified: first, last, every click frame and the 4
  frames on each side of it.
- For every other frame `i`, with `a = p[i] - p[i-1]` and `b = p[i+1] - p[i]`:
  `score = angle(a, b) / (t[i+1] - t[i-1])` (degrees per second), multiplied by
  `8 / (8 + min(|a|, |b|))` so that large motions are trusted more.
  Zero-length segments score 0.
- Threshold = intensity clamped to `[100, 2000]` (default 100).
- Frames above the threshold are replaced by time-linear interpolation between
  the nearest kept frames on each side.

## §7 Cursor spring

A 2D spring (§1, cursor preset) follows the smoothed cursor, starting at the
first sample with zero velocity. Its output is the cursor position the camera
reasons about.

## §8 Zoom scale spring

Step targets: `1.0` until each segment's start frame, the segment zoom until
its end frame, and `1.0` after the last segment. A start entry is only added
when it is after frame 0 and after the previous entry. The spring (camera
preset) produces `scale[i] = max(x, 0)`.

## §9 Dwell-cluster camera anchors

The camera must not chase every cursor movement. Inside each zoom segment the
focus points (§7, or §12 while typing) are grouped into dwell clusters:

- Visible half-size at zoom `z`: `hw = W / z / 2`, `hh = H / z / 2`.
- A cluster grows frame by frame and closes when the point drifts more than
  `hw` / `hh` from the cluster's first point, when the cluster's bounding box
  exceeds `hw × hh`, when the focus source switches between cursor and typing
  target, or at the end of the segment.
- Each closed cluster emits a target at its last frame: the anchor (§10) for
  the bounding-box centre, normalised by `W`, `H`, using `max(1.01, z)`.
- When a segment starts while the scale is still ~1.0 (`|scale - 1| < 0.01`),
  its first target is applied instantly, so the camera does not visibly pan
  while fully zoomed out.
- Before a new segment begins, the previous target is held until that
  segment's start frame.
- All other targets are followed by a 2D spring (camera preset). With no
  segments the anchor is the frame centre.

## §10 Edge clamping and crop

Zoom is a scale about an anchor point `A` in normalised frame coordinates.
For a desired visible centre `t` and scale `s > 1`:

```
A = s / (s - 1) * (t - 1 / (2 s))      clamped to [0, 1] per axis
```

With `A` in `[0, 1]`, the visible window `[A (1 - 1/s), A (1 - 1/s) + 1/s]`
always lies inside the frame, so the zoom never shows anything outside the
screen. The per-frame crop in video pixels is:

```
s' = max(scale, 1)        w = W / s'      h = H / s'
x  = A.x * (W - w)        y = A.y * (H - h)
```

## §11 Rendering

Each crop series `x, y, w, h` over `t` is simplified with Ramer-Douglas-Peucker
(default tolerance 0.05 px, measured on the value axis) and compiled into a
balanced binary tree of `if(lt(t,T),LEFT,RIGHT)` with linear interpolation in
the leaves. FFmpeg's `libplacebo` filter evaluates these expressions per frame
with sub-pixel GPU sampling and scales the crop back to the source resolution.

## §12 Typing focus target

Wayland does not expose the text caret. While typing, from 0.5 s before a
typing range until its end, the focus point is replaced by the position of the
most recent click at or before the range start (normally the text field that
was clicked), or by the cursor at the range start when there was no click.

## §13 Capture

- Cursor: Hyprland `cursorpos` over the request socket every 16 ms, in logical
  coordinates, scaled into video pixels using the capture rectangle.
- Clicks and keystrokes: temporary non-consuming Hyprland binds that append a
  line to a FIFO; the daemon timestamps lines with the monotonic clock. Only
  keystroke times are stored, never key names.
- Alignment: the video is assumed to start at `t_stop - video_duration`.
