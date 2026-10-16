# WayZoom

Cinematic auto-zoom screen recorder for Hyprland (Wayland). Record with one
key, stop with the same key, and get a video that smoothly zooms in on every
click and while you type, and pans with the cursor — Screen Studio / FocuSee
style, without Electron.

Pure Python 3 standard library on top of `wf-recorder`, `slurp`, `ffmpeg` and
Hyprland's IPC sockets. No root, no `input` group, no pip dependencies.

## Requirements

| Tool | Used for |
|---|---|
| Hyprland with Lua config (tested on 0.56.2) | cursor position, events, click capture via `hyprctl eval` |
| `wf-recorder` | screen capture |
| `slurp` | region selection (`--region`) |
| `ffmpeg` / `ffprobe` with **libplacebo** (Vulkan) | sub-pixel GPU zoom when rendering |
| `pactl` (libpulse; PipeWire with `pipewire-pulse`) | desktop/microphone audio (`--audio`) |
| `notify-send` (optional) | desktop notifications |
| Python ≥ 3.12 | everything else |

## Compatibility

WayZoom depends on **Hyprland**, not on a particular distribution: it talks to
Hyprland's IPC sockets and installs temporary binds with `hyprctl eval`, which
requires Hyprland's **Lua configuration**.

| Status | Environment |
|---|---|
| Tested | [Omarchy](https://omarchy.org/) (Arch Linux), Hyprland 0.56.2 with Lua config, Python 3.14, FFmpeg 9.0 |
| Expected to work (untested) | Other Arch-based distributions, Fedora, NixOS, openSUSE, etc. running Hyprland with Lua config and the requirements above |
| Not supported | Hyprland with the legacy hyprlang config (`hyprctl keyword`), Sway, river, GNOME, KDE Plasma, X11 |

FFmpeg must be built with `libplacebo` and a working Vulkan driver
(`ffmpeg -h filter=libplacebo` should print the filter options).

On Arch Linux / Omarchy:

```bash
sudo pacman -S --needed wf-recorder slurp ffmpeg python libnotify
```

## Install

```bash
./install.sh          # symlinks bin/wayzoom into ~/.local/bin
```

Then bind it to a key in your Hyprland Lua configuration, for example in
`~/.config/hypr/bindings.lua`:

```lua
hl.bind("SUPER + ALT + Z", hl.dsp.exec_cmd("wayzoom toggle"))
hl.bind("SUPER + ALT + SHIFT + Z", hl.dsp.exec_cmd("wayzoom toggle --region"))
```

On [Omarchy](https://omarchy.org/) you can use its helper instead:
`o.bind("SUPER + ALT + Z", "WayZoom record screen", "wayzoom toggle")`.

## Usage

```text
wayzoom toggle [--region] [--audio {both,desktop,mic,none} | --no-audio] [--no-typing-zoom] [--zoom 2.0] [--zoom-speed P] [--cursor-speed P]
wayzoom start  [same options]       start recording in the background
wayzoom stop                        stop, render and notify with the output path
wayzoom status                      idle | recording | rendering | stale pidfile
wayzoom render SESSION_DIR [--zoom Z] [--zoom-speed P] [--cursor-speed P] [--output PATH] [--plan] [--no-typing-zoom]
wayzoom cleanup                     remove click/key binds, audio modules and stale runtime files
```

Spring presets `P`: `slow` (default camera), `mellow` (default cursor),
`quick`, `rapid`. `render --plan` prints the planned zoom segments and the
detected typing ranges as JSON without encoding, which is handy for
re-rendering a session with other settings. `--no-typing-zoom` on
`start`/`toggle` disables keystroke capture entirely; on `render` it ignores
the recorded keystrokes (by default `render` follows the recording's setting).

Files:

- Output: `${XDG_VIDEOS_DIR:-~/Videos}/wayzoom/wayzoom-<timestamp>.mp4`
- Session: `${XDG_VIDEOS_DIR:-~/Videos}/wayzoom/.sessions/<timestamp>/` with
  `raw.mkv`, `cursor.csv`, `clicks.csv`, `keys.csv`, `events.log`,
  `meta.json` and logs.
- Runtime: `$XDG_RUNTIME_DIR/wayzoom/` (`recorder.pid`, `state`, `clicks.fifo`,
  `bound_keys`, `audio_modules`).

### Audio

`--audio` picks what is recorded (default `both`):

| Mode | Records |
|---|---|
| `both` | speakers and microphone, mixed into one track |
| `desktop` | what the default output plays (its monitor) |
| `mic` | the default input |
| `none` / `--no-audio` | no audio |

`both` loads a temporary virtual sink, shown as **WayZoom-mix** in audio
mixers while recording, plus two loopbacks feeding it from the default output's
monitor and the default input. Your default output and input are never
changed; the modules are unloaded when the recording stops. Their ids are kept
in `$XDG_RUNTIME_DIR/wayzoom/audio_modules`, so after a hard kill the next
recording, or `wayzoom cleanup`, unloads them.

This needs `pactl` talking to PulseAudio or PipeWire with `pipewire-pulse`.
Without it, or without a default output/input, WayZoom falls back to whatever
side is available (or no audio) and logs a warning.

### Typing zoom and privacy

While recording, WayZoom binds the unmodified text keys `a`–`z`, `0`–`9`,
`space`, `ntilde`, `comma`, `period` and `minus` as non-consuming binds, so
your typing still reaches the focused window. Each bind writes a bare `key`
line to the FIFO: **only the time of each keystroke is stored (`keys.csv`
has a single `t_mono` column); which key was pressed is never recorded.**
Modified strokes (Shift, Ctrl, Alt, …) are not captured.

Keys that already have an unmodified bind in any submap are skipped (and
logged), so stopping never removes one of your binds. The keys actually bound
are listed in `$XDG_RUNTIME_DIR/wayzoom/bound_keys` until they are unbound;
`wayzoom cleanup` only unbinds the keys listed there.

## How it works

The full motion model (springs, presets, zoom planning, camera clustering,
edge clamping) is specified in [DESIGN.md](DESIGN.md).

```text
record   wf-recorder ─────────────► raw.mkv
         cursorpos every 16 ms ───► cursor.csv
         mouse binds → FIFO ──────► clicks.csv
         key binds → FIFO ────────► keys.csv (timestamps only)
render   resample → smooth → cursor spring → typing ranges → zoom segments
         → scale spring → substitute caret → dwell-cluster anchors → crop per frame
         → piecewise-linear expressions of t → ffmpeg libplacebo
```

1. **Capture.** A detached daemon runs `wf-recorder` (focused monitor or a
   `slurp` region), polls `cursorpos` on `.socket.sock` every 16 ms, logs
   `.socket2.sock` events, and installs non-consuming `mouse:272`/`mouse:273`
   binds through `hyprctl eval` that append `down|up <button>` to a FIFO, plus
   the typing key binds described above. The daemon timestamps everything
   with the monotonic clock.
2. **Alignment.** The video starts at `t_stop − duration`; logical cursor
   coordinates are scaled into video pixels (handles fractional monitor scale).
3. **Planning** (all on a fixed 16 ms grid): linear resampling, jitter
   smoothing, a 2D cursor spring, typing ranges (strokes less than 1.5 s
   apart; at least 5 strokes or 1.5 s long), zoom segments around each click
   (−0.32 s / +1.266 s) and each typing range (−0.5 s / end), merged when
   closer than 2.6 s, none in the last second, a zoom-scale spring, and a
   camera anchor that only re-targets when its target leaves roughly half of
   the visible viewport. While typing (from 0.5 s before a range to its end)
   the target is a substitute caret: the position of the last click before
   the range, or the cursor at the range start when there was none. Anchors
   are clamped so the crop never leaves the frame.
4. **Rendering.** Each crop series (`x`, `y`, `w`, `h`) is simplified
   (Ramer-Douglas-Peucker, 0.05 px) and compiled into a balanced
   `if(lt(t,T),…)` expression. libplacebo evaluates these per frame with
   sub-pixel GPU sampling and scales back to the source resolution. The
   filter graph is passed with `-/vf <file>`.

## Limitations

- Wayland exposes no caret position, so typing zoom targets the last click
  (usually the text field you clicked into), not the actual caret.
- `events.log` is recorded for diagnostics only; the planner ignores it.
- Zoom scales below 1.0 (zoom-out with a background) are rendered as the full
  frame.
- `hl.unbind("mouse:272")` / `("mouse:273")` removes *all* unmodified binds on
  those buttons while stopping; modified binds such as `SUPER + drag` survive.
  If you have your own unmodified mouse-button binds, re-add them afterwards.
- If the daemon is killed hard, run `wayzoom cleanup` to remove the click and
  key binds and the audio mix (the next recording also removes stale key binds
  and audio modules first).
- No motion blur, cursor restyling or background/padding.

## Development

```bash
python3 -m unittest discover -s tests -v
```

The domain layer (`wayzoom/domain/`) is pure and fully unit tested; adapters
(`wayzoom/adapters/`) wrap external tools; `wayzoom/app/` orchestrates them.

## License

[MIT](LICENSE)
