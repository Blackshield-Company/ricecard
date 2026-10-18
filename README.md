# Ricecard

Turn a desktop screenshot and an [Omarchy](https://omarchy.org) theme into
ready-to-post showcase images. Part of **Glyphsmith**.

Ricecard reads the theme's `colors.toml`, then lays your screenshot in a
rounded card with a soft shadow and an accent glow. The card sits over a blurred,
darkened backdrop, with a pill-shaped strip of palette swatches underneath. You
get a 1600x900 and a 1200x1200 PNG by default, sized for X, Mastodon, Reddit
and r/unixporn.

By default the image has **no text**. A title and an install line are opt-in.

## Install

Needs Python 3.8+ and Pillow. Nothing else.

```sh
# from this directory
pipx install .          # or: pip install --user .
ricecard --help
```

You can also skip installing and run the script directly:

```sh
python3 ricecard.py --help
```

## Usage

```sh
# Screenshot + the current Omarchy theme (~/.config/omarchy/current/theme)
ricecard ~/Pictures/desktop.png

# Grab the screen now with grim (Wayland), after a 2 s delay, one monitor only
# NOTE: --grab is untested on a real desktop so far.
ricecard --grab --delay 2 --grab-output DP-1

# A specific theme, a custom output dir and name
ricecard shot.png --theme ~/src/neon-horizon --out ./previews --name neon

# Choose sizes: WxH values or presets
ricecard shot.png --sizes 1600x900,1200x1200,1080x1920
ricecard shot.png --sizes wide,square,story

# Add a title and an install line (both off by default)
ricecard shot.png --title "Neon Horizon" \
  --install-line "omarchy-theme-install https://github.com/you/neon-horizon"

# Up to two extra images as tilted side cards
ricecard shot.png --extras lock.png terminal.png

# Use the screenshot itself (or any image) as the backdrop
ricecard shot.png --backdrop screenshot
ricecard shot.png --backdrop ~/Pictures/wall.png
```

Each output is written as `<name>-<W>x<H>.png`, and its path is printed on stdout.
`<name>` defaults to the theme directory's name. For the current-theme symlink,
Ricecard follows the link, or reads `theme.name` when the theme is a copied
directory.

### Options

| Option | Default | Description |
| --- | --- | --- |
| `SCREENSHOT` / `--grab` | (one of the two is required) | Input image, or take one with `grim` (`--grab` is untested on a real desktop so far) |
| `--grab-output NAME` | all outputs | Monitor passed to `grim -o` |
| `--delay SECONDS` | `0` | Wait before `--grab` |
| `-t, --theme DIR` | `~/.config/omarchy/current/theme` | Theme dir containing `colors.toml` |
| `-o, --out DIR` | `.` | Output directory (created if missing) |
| `-n, --name NAME` | theme name | Output filename prefix |
| `-s, --sizes LIST` | `1600x900,1200x1200` | Comma- or space-separated `WxH` values or presets |
| `-b, --backdrop` | `auto` | `auto` (first image in `backgrounds/`, else the screenshot), `theme`, `screenshot`, or an image path |
| `--title TEXT` | off | Title at the top |
| `--install-line TEXT` | off | Command shown in a monospace pill above the swatches |
| `--extras IMG [IMG]` | none | Up to 2 tilted side cards |
| `--swatches N` | `9` | Maximum number of palette swatches |
| `--no-glow` | glow on | Disable the accent-coloured glow behind the main card |
| `--blur R` | `10` (`16` for a screenshot backdrop) | Backdrop blur radius |
| `--dim F` | `0.62` | Backdrop brightness, 0..1 |
| `--supersample N` | `2` | Render at N times the size, then downscale for clean edges |
| `--font PATH` / `--mono-font PATH` | system fonts via `fc-match` | Fonts for the title and the install line |

Presets: `wide` 1600x900, `square` 1200x1200, `hd` 1920x1080,
`portrait` 1080x1350, `story` 1080x1920, `og` 1200x630.

### Layout

* **Landscape** sizes (W/H ≥ 1.25): the screenshot is centred. With extras, the
  main card moves right and the extras fan out on the left.
* **Square and portrait** sizes: the screenshot sits on top and the extras go
  underneath.
* Images of any aspect ratio are scaled to fit and never cropped. Only the
  backdrop is cover-cropped.

### Palette

The swatches are `accent`, `red`, `orange`, `yellow`, `green`, `cyan`, `blue`,
`magenta` and `foreground`, in that order, with near-duplicates dropped.
Missing keys are handled gracefully:

* Named ANSI keys fall back to `color0`–`color15` where those exist.
* `darker_background`, `lighter_background` and the border colour are derived
  from `background`/`foreground` when absent.
* `accent` falls back to `hyprland_active_border`, then `blue`/`cyan`/`magenta`.
* A missing, empty or malformed `colors.toml` gives a warning on stderr and a
  neutral dark palette, never a crash.

Accepted colour formats: `#rrggbb`, `#rgb`, `#rrggbbaa`, `rrggbb`, `0xrrggbb`,
`rgb(...)`/`rgba(...)` (hex or decimal), and Hyprland gradients, where the first
stop is used.

## Development

```sh
python3 -m venv .venv && .venv/bin/pip install pillow pytest
.venv/bin/python -m pytest
```

The tests render the `blackshield` and `neon-horizon` themes from
`../../themes/`, using each theme's `preview.png` as the screenshot.

## License

MIT © synthalorian
