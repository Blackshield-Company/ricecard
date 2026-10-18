#!/usr/bin/env python3
"""Ricecard: turn a desktop screenshot and an Omarchy theme into showcase images.

Part of Glyphsmith. MIT licensed, (c) synthalorian.
Python 3 + Pillow only. Run directly (``python3 ricecard.py``) or via the
``ricecard`` console script.
"""
from __future__ import annotations

import argparse
import dataclasses
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

__version__ = "0.1.0"

DEFAULT_THEME = "~/.config/omarchy/current/theme"
DEFAULT_SIZES = "1600x900,1200x1200"
SIZE_PRESETS = {
    "wide": (1600, 900),
    "square": (1200, 1200),
    "hd": (1920, 1080),
    "portrait": (1080, 1350),
    "story": (1080, 1920),
    "og": (1200, 630),
}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}

# Fallback palette (neutral dark) used for any key the theme does not define.
FALLBACK = {
    "background": "#16161c",
    "foreground": "#d8d8e0",
    "accent": "#7aa2f7",
}
# Swatch order: the accent first, then the ANSI hues, then the foreground.
SWATCH_KEYS = ["accent", "red", "orange", "yellow", "green", "cyan", "blue", "magenta", "foreground"]
# Omarchy/kitty-style colorN fallbacks for named ANSI keys.
ANSI_ALIASES = {
    "red": ["color1"], "green": ["color2"], "yellow": ["color3"], "blue": ["color4"],
    "magenta": ["color5"], "cyan": ["color6"], "foreground": ["color7", "color15"],
    "background": ["color0"], "muted": ["color8"],
    "bright_red": ["color9"], "bright_green": ["color10"], "bright_yellow": ["color11"],
    "bright_blue": ["color12"], "bright_magenta": ["color13"], "bright_cyan": ["color14"],
}


class RicecardError(Exception):
    """A user-facing error (bad input, missing file, ...)."""


def warn(msg: str) -> None:
    print(f"ricecard: warning: {msg}", file=sys.stderr)


# --------------------------------------------------------------------------- colors

RGB = tuple  # (r, g, b)


def parse_color(value) -> RGB | None:
    """Parse '#rrggbb', '#rgb', '#rrggbbaa', 'rrggbb', '0xrrggbb', 'rgb(rrggbb)',
    'rgba(rrggbbaa)' or 'rgb(r, g, b)'. Returns an (r, g, b) tuple or None."""
    if not isinstance(value, str):
        return None
    s = value.strip().lower()
    # Hyprland gradients: "rgba(ff4fa3ee) rgba(4fe3f0ee) 45deg" -> take the first stop.
    m = re.match(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)", s)
    if m:
        vals = [int(v) for v in m.groups()]
        return tuple(vals) if all(0 <= v <= 255 for v in vals) else None
    m = re.match(r"rgba?\(\s*([0-9a-f]{6,8})\s*\)", s)
    if m:
        s = m.group(1)
    s = s.split()[0] if s else s
    if s.startswith("#"):
        s = s[1:]
    elif s.startswith("0x"):
        s = s[2:]
    if not re.fullmatch(r"[0-9a-f]{3}|[0-9a-f]{6}|[0-9a-f]{8}", s or ""):
        return None
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))


def to_hex(rgb: RGB) -> str:
    return "#%02x%02x%02x" % tuple(rgb[:3])


def mix(a: RGB, b: RGB, t: float) -> RGB:
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def rgba(rgb: RGB, a: int = 255) -> tuple:
    return tuple(rgb[:3]) + (a,)


def luminance(rgb: RGB) -> float:
    return (0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]) / 255


def _simple_toml(text: str) -> dict:
    """Very small TOML subset parser: top-level `key = "value"` / `key = 'value'` lines.
    Used when tomllib is unavailable or the file is not valid TOML."""
    out = {}
    for line in text.splitlines():
        m = re.match(r"""\s*([A-Za-z0-9_.-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^#\s]+))""", line)
        if m:
            out[m.group(1)] = m.group(2) if m.group(2) is not None else (
                m.group(3) if m.group(3) is not None else m.group(4))
    return out


def read_colors_toml(path: Path) -> dict:
    """Read colors.toml into a flat dict of str -> str. Never raises on bad content."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        warn(f"cannot read {path}: {e}; using fallback palette")
        return {}
    data = None
    try:
        import tomllib  # Python 3.11+
        data = tomllib.loads(text)
    except ImportError:
        pass
    except Exception as e:  # malformed TOML: be forgiving
        warn(f"{path} is not valid TOML ({e}); falling back to a line parser")
    if data is None:
        data = _simple_toml(text)
    flat = {}
    for k, v in data.items():
        if isinstance(v, dict):  # tolerate [colors] style tables
            for k2, v2 in v.items():
                flat.setdefault(str(k2), v2)
        else:
            flat[str(k)] = v
    return flat


@dataclass
class Palette:
    background: RGB
    darker: RGB
    lighter: RGB
    foreground: RGB
    accent: RGB
    border: RGB
    swatches: list = field(default_factory=list)
    missing: list = field(default_factory=list)

    @classmethod
    def from_mapping(cls, raw: dict, max_swatches: int = 9) -> "Palette":
        missing = []

        def get(key, *alts):
            for k in (key, *ANSI_ALIASES.get(key, []), *alts):
                if k in raw:
                    c = parse_color(raw[k])
                    if c is not None:
                        return c
                    warn(f"ignoring unparseable color {k} = {raw[k]!r}")
            return None

        bg = get("background")
        if bg is None:
            missing.append("background")
            bg = parse_color(FALLBACK["background"])
        fg = get("foreground", "light_foreground", "bright_foreground")
        if fg is None:
            missing.append("foreground")
            fg = parse_color(FALLBACK["foreground"])
            if luminance(bg) > 0.5:  # light theme without a foreground
                fg = (32, 32, 40)
        darker = get("darker_background", "dark_background") or mix(bg, (0, 0, 0), 0.45)
        lighter = get("lighter_background", "selection") or mix(bg, fg, 0.08)
        accent = get("accent", "hyprland_active_border", "blue", "cyan", "magenta")
        if accent is None:
            missing.append("accent")
            accent = parse_color(FALLBACK["accent"])
        border = mix(lighter, fg, 0.2)

        sw = []
        for key in SWATCH_KEYS:
            c = {"accent": accent, "foreground": fg}.get(key) or get(key)
            if c is None:
                if key not in ("accent", "foreground"):
                    missing.append(key)
                continue
            if all(sum(abs(c[i] - o[i]) for i in range(3)) > 18 for o in sw):
                sw.append(c)
        if len(sw) < 3:  # sparse palette: fill from whatever colors exist
            for k, v in raw.items():
                c = parse_color(v)
                if c and all(sum(abs(c[i] - o[i]) for i in range(3)) > 18 for o in sw) \
                        and abs(luminance(c) - luminance(bg)) > 0.15:
                    sw.append(c)
                if len(sw) >= 8:
                    break
        if not sw:
            sw = [accent, fg]
        return cls(bg, darker, lighter, fg, accent, border, sw[:max(1, max_swatches)], missing)


def load_palette(theme_dir: Path | None, max_swatches: int = 9) -> Palette:
    raw = {}
    if theme_dir is not None:
        p = Path(theme_dir) / "colors.toml"
        if p.is_file():
            raw = read_colors_toml(p)
        else:
            warn(f"no colors.toml in {theme_dir}; using fallback palette")
    return Palette.from_mapping(raw, max_swatches)


# --------------------------------------------------------------------------- theme helpers

def first_background(theme_dir: Path | None) -> Path | None:
    if theme_dir is None:
        return None
    d = Path(theme_dir) / "backgrounds"
    if not d.is_dir():
        return None
    files = sorted(p for p in d.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS)
    return files[0] if files else None


def theme_name(theme_dir: Path | None) -> str:
    if theme_dir is None:
        return "ricecard"
    real = Path(theme_dir).expanduser().resolve()
    name = real.name
    if name in ("theme", "current", ""):
        for cand in (real.parent / "theme.name", Path(theme_dir).expanduser().parent / "theme.name"):
            try:
                n = cand.read_text().strip()
                if n:
                    return slug(n)
            except OSError:
                pass
        return "ricecard"
    return slug(name)


def slug(s: str) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", s.strip().lower()).strip("-.")
    return s or "ricecard"


def parse_sizes(spec: str) -> list:
    sizes = []
    for part in re.split(r"[,\s]+", spec.strip()):
        if not part:
            continue
        if part.lower() in SIZE_PRESETS:
            sizes.append(SIZE_PRESETS[part.lower()])
            continue
        m = re.fullmatch(r"(\d+)[xX*](\d+)", part)
        if not m:
            raise RicecardError(
                f"bad size {part!r}: use WxH (e.g. 1600x900) or a preset ({', '.join(SIZE_PRESETS)})")
        w, h = int(m.group(1)), int(m.group(2))
        if not (200 <= w <= 8000 and 200 <= h <= 8000):
            raise RicecardError(f"size {part} out of range (200..8000 per side)")
        sizes.append((w, h))
    if not sizes:
        raise RicecardError("no sizes given")
    return list(dict.fromkeys(sizes))


def open_image(path) -> Image.Image:
    try:
        im = Image.open(path)
        im.load()
    except FileNotFoundError:
        raise RicecardError(f"image not found: {path}")
    except Exception as e:
        raise RicecardError(f"cannot open image {path}: {e}")
    if im.mode not in ("RGB", "RGBA"):
        im = im.convert("RGBA" if "A" in im.getbands() or im.mode == "P" else "RGB")
    return im


def grab_screenshot(output: str | None = None, delay: float = 0) -> Path:
    if not shutil.which("grim"):
        raise RicecardError("--grab needs grim (Wayland). Install grim or pass a PNG path.")
    if not os.environ.get("WAYLAND_DISPLAY"):
        raise RicecardError("--grab needs a Wayland session (WAYLAND_DISPLAY is not set)")
    if delay > 0:
        time.sleep(delay)
    fd, tmp = tempfile.mkstemp(prefix="ricecard-grab-", suffix=".png")
    os.close(fd)
    cmd = ["grim"] + (["-o", output] if output else []) + [tmp]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RicecardError(f"grim failed: {r.stderr.strip() or r.returncode}")
    return Path(tmp)


# --------------------------------------------------------------------------- fonts

def find_font(kind: str = "sans-bold", override: str | None = None) -> str | None:
    if override:
        if Path(override).is_file():
            return override
        warn(f"font {override} not found; using a system font")
    pattern = {"sans-bold": "sans:bold", "mono": "monospace"}[kind]
    if shutil.which("fc-match"):
        try:
            r = subprocess.run(["fc-match", "-f", "%{file}", pattern],
                               capture_output=True, text=True, timeout=5)
            if r.returncode == 0 and r.stdout and Path(r.stdout).is_file():
                return r.stdout
        except (OSError, subprocess.SubprocessError):
            pass
    cands = {
        "sans-bold": ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                      "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
                      "/usr/share/fonts/liberation/LiberationSans-Bold.ttf",
                      "/usr/share/fonts/noto/NotoSans-Bold.ttf"],
        "mono": ["/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
                 "/usr/share/fonts/TTF/DejaVuSansMono.ttf",
                 "/usr/share/fonts/liberation/LiberationMono-Regular.ttf"],
    }[kind]
    return next((c for c in cands if Path(c).is_file()), None)


def load_font(path: str | None, size: int, bold: bool = False):
    if path:
        try:
            f = ImageFont.truetype(path, size)
            if bold:  # variable fonts (Inter, Noto) default to a light/regular instance
                try:
                    f.set_variation_by_name("Bold")
                except Exception:
                    pass
            return f
        except OSError:
            pass
    try:
        return ImageFont.load_default(size=size)  # Pillow >= 10.1
    except TypeError:
        return ImageFont.load_default()


# --------------------------------------------------------------------------- drawing primitives

def cover(im: Image.Image, w: int, h: int) -> Image.Image:
    r = max(w / im.width, h / im.height)
    im = im.resize((max(w, math.ceil(im.width * r)), max(h, math.ceil(im.height * r))), Image.LANCZOS)
    x = (im.width - w) // 2
    y = (im.height - h) // 2
    return im.crop((x, y, x + w, y + h))


def backdrop(src: Image.Image, W: int, H: int, pal: Palette, S: int,
             blur: float = 10, dim: float = 0.62) -> Image.Image:
    # Downscale before blurring: same look, much faster for large radii.
    f = 4
    small = cover(src.convert("RGB"), max(1, W // f), max(1, H // f))
    small = small.filter(ImageFilter.GaussianBlur(max(0.1, blur * S / f)))
    bg = small.resize((W, H), Image.BICUBIC)
    bg = ImageEnhance.Brightness(bg).enhance(dim)
    # Vignette toward darker_background.
    vw, vh = max(1, W // f), max(1, H // f)
    vig = Image.new("L", (vw, vh), 0)
    ImageDraw.Draw(vig).ellipse((-vw * 0.25, -vh * 0.35, vw * 1.25, vh * 1.3), fill=255)
    vig = vig.filter(ImageFilter.GaussianBlur(min(vw, vh) * 0.18)).resize((W, H), Image.BICUBIC)
    dark = Image.new("RGB", (W, H), pal.darker)
    return Image.composite(bg, dark, vig).convert("RGBA")


def rmask(w: int, h: int, r: int) -> Image.Image:
    m = Image.new("L", (w, h), 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, w - 1, h - 1), r, fill=255)
    return m


def fit(iw: int, ih: int, bw: float, bh: float) -> tuple:
    """Largest (w, h) with aspect iw:ih inside a bw x bh box."""
    r = min(bw / iw, bh / ih)
    return max(1, round(iw * r)), max(1, round(ih * r))


def card(src: Image.Image, w: int, h: int, radius: int, border: RGB, bw: int) -> Image.Image:
    im = src.convert("RGBA").resize((w, h), Image.LANCZOS)
    radius = min(radius, w // 2, h // 2)
    c = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    c.paste(im, (0, 0), rmask(w, h, radius))
    if bw:
        ImageDraw.Draw(c).rounded_rectangle((0, 0, w - 1, h - 1), radius, outline=rgba(border, 200), width=bw)
    return c


def place(canvas: Image.Image, c: Image.Image, cx: float, cy: float, pal: Palette,
          angle: float = 0, shadow: int = 40, salpha: int = 170, glow: tuple | None = None) -> None:
    if angle:
        c = c.rotate(angle, resample=Image.BICUBIC, expand=True)
    shadow = max(1, int(shadow))
    pad = shadow * 3
    x = int(cx - c.width / 2)
    y = int(cy - c.height / 2)
    # Shadow mask computed at reduced resolution (blur is the expensive part).
    f = 4
    a = c.split()[3].resize((max(1, c.width // f), max(1, c.height // f)), Image.BILINEAR)
    sm = Image.new("L", ((c.width + 2 * pad) // f, (c.height + 2 * pad) // f), 0)
    sm.paste(a, (pad // f, pad // f))
    sm = sm.filter(ImageFilter.GaussianBlur(shadow / f)).resize((c.width + 2 * pad, c.height + 2 * pad), Image.BICUBIC)
    shl = sm.point(lambda v: v * salpha // 255)
    if glow:
        gm = shl.point(lambda v: v * glow[1] // 255)
        canvas.alpha_composite(Image.merge("RGBA", [*Image.new("RGB", sm.size, glow[0]).split(), gm]),
                               (x - pad, y - pad))
    canvas.alpha_composite(Image.merge("RGBA", [*Image.new("RGB", sm.size, pal.darker).split(), shl]),
                           (x - pad, y - pad + shadow // 2))
    canvas.alpha_composite(c, (x, y))


def swatches(canvas: Image.Image, colors: list, cx: float, cy: int, size: int, gap: int,
             pal: Palette, S: int) -> tuple:
    """Pill-shaped tray of palette dots. Returns the tray bbox."""
    n = len(colors)
    total = n * size + (n - 1) * gap
    x0 = int(cx - total / 2)
    pad = int(size * 0.45)
    box = (x0 - pad, cy - size // 2 - pad, x0 + total + pad, cy + size // 2 + pad)
    tray = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    td = ImageDraw.Draw(tray)
    td.rounded_rectangle(box, size // 2 + pad, fill=rgba(mix(pal.darker, pal.background, 0.5), 150),
                         outline=rgba(pal.border, 110), width=S)
    for i, c in enumerate(colors):
        x = x0 + i * (size + gap)
        td.ellipse((x, cy - size // 2, x + size, cy + size // 2), fill=rgba(c))
        # subtle ring so dark swatches stay visible on a dark tray
        td.ellipse((x, cy - size // 2, x + size, cy + size // 2), outline=rgba(pal.foreground, 40), width=max(1, S))
    canvas.alpha_composite(tray)
    return box


def text_center(canvas: Image.Image, text: str, cx: float, top: int, font, fill, shadow_rgb, S: int) -> int:
    d = ImageDraw.Draw(canvas)
    l, t, r, b = d.textbbox((0, 0), text, font=font)
    x = int(cx - (r - l) / 2 - l)
    y = top - t
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).text((x, y + 3 * S), text, font=font, fill=rgba(shadow_rgb, 170))
    canvas.alpha_composite(layer.filter(ImageFilter.GaussianBlur(5 * S)))
    d.text((x, y), text, font=font, fill=fill)
    return b - t


# --------------------------------------------------------------------------- composition

@dataclass
class Options:
    sizes: list = field(default_factory=lambda: parse_sizes(DEFAULT_SIZES))
    backdrop: str = "auto"          # auto | theme | screenshot | <path>
    title: str | None = None
    install_line: str | None = None
    extras: list = field(default_factory=list)
    supersample: int = 2
    glow: bool = True
    blur: float = 10
    dim: float = 0.62
    max_swatches: int = 9
    font: str | None = None
    mono_font: str | None = None


def _fit_text(font_path, text, max_w, size, S, bold=False):
    font = load_font(font_path, size, bold)
    probe = ImageDraw.Draw(Image.new("L", (1, 1)))
    while size > 8 * S:
        l, _, r, _ = probe.textbbox((0, 0), text, font=font)
        if r - l <= max_w:
            break
        size = int(size * 0.92)
        font = load_font(font_path, size, bold)
    return font


def render(screenshot: Image.Image, pal: Palette, size: tuple, opts: Options,
           backdrop_img: Image.Image | None = None, extras: list | None = None) -> Image.Image:
    """Render one showcase image of `size` (W, H). Pure function of its inputs."""
    S = max(1, int(opts.supersample))
    W0, H0 = size
    W, H = W0 * S, H0 * S
    U = min(W, H)  # unit for size-independent spacing
    extras = (extras or [])[:2]

    cv = backdrop(backdrop_img or screenshot, W, H, pal, S, opts.blur, opts.dim)

    margin = int(U * 0.055)
    top = margin
    bottom = H - margin

    # Swatch tray at the bottom.
    n = len(pal.swatches)
    sw_size = int(U * 0.022)
    sw_gap = int(U * 0.015)
    max_total = W * 0.8
    if n * sw_size + (n - 1) * sw_gap > max_total:
        k = max_total / (n * sw_size + (n - 1) * sw_gap)
        sw_size, sw_gap = int(sw_size * k), int(sw_gap * k)
    sw_pad = int(sw_size * 0.45)
    sw_cy = bottom - sw_size // 2 - sw_pad
    bottom = sw_cy - sw_size // 2 - sw_pad - int(U * 0.035)

    # Optional install line, drawn as a pill just above the swatches.
    install = None
    if opts.install_line:
        mono = find_font("mono", opts.mono_font)
        ifont = _fit_text(mono, opts.install_line, W * 0.7, int(U * 0.024), S)
        d = ImageDraw.Draw(cv)
        l, t, r, b = d.textbbox((0, 0), opts.install_line, font=ifont)
        ph, pw = (b - t) + int(U * 0.022), (r - l) + int(U * 0.05)
        install = (ifont, (l, t, r, b), pw, ph, bottom - ph)
        bottom -= ph + int(U * 0.03)

    # Optional title at the top.
    if opts.title:
        tfont = _fit_text(find_font("sans-bold", opts.font), opts.title, W * 0.86, int(U * 0.058), S, bold=True)
        th = text_center(cv, opts.title, W / 2, top, tfont, rgba(pal.foreground), pal.darker, S)
        top += th + int(U * 0.04)

    rh = bottom - top
    rcy = top + rh / 2
    landscape = W / H >= 1.25
    border_w = max(1, S)
    hero_radius = int(U * 0.02)
    side_radius = int(U * 0.016)
    glow = (pal.accent, 60) if opts.glow else None
    sw_, sh_ = screenshot.size

    side_cards = []  # (img, w, h, cx, cy, angle)
    if not extras:
        hw, hh = fit(sw_, sh_, W * (0.74 if landscape else 0.86), rh * 0.96)
        hcx, hcy = W / 2, rcy
    elif landscape:
        hw, hh = fit(sw_, sh_, W * 0.58, rh * 0.98)
        hcx, hcy = W * (0.548 if len(extras) > 1 else 0.56), rcy
        # (width frac, centre x frac, centre y frac within region, angle), from the
        # hand-tuned blackshield/neon layouts.
        slots = [(0.235, 0.178, 0.33, 7), (0.27, 0.215, 0.78, -4)] if len(extras) > 1 \
            else [(0.30, 0.20, 0.55, 5)]
        for im, (wf, xf, yf, ang) in zip(extras, slots):
            ew, eh = fit(im.width, im.height, W * wf, rh * (0.5 if len(extras) > 1 else 0.7))
            ecx = max(margin * 0.6 + ew / 2, W * xf)
            ecy = min(max(top + rh * yf, top + eh / 2), bottom - eh / 2)
            side_cards.append((im, ew, eh, ecx, ecy, ang))
    else:
        hw, hh = fit(sw_, sh_, W * 0.84, rh * 0.6)
        hcy = top + hh / 2
        hcx = W / 2
        low_top = hcy + hh / 2 - rh * 0.04
        low_h = bottom - low_top
        slots = [(0.44, 0.29, 4), (0.36, 0.725, -5)] if len(extras) > 1 else [(0.56, 0.5, 3)]
        for im, (wf, xf, ang) in zip(extras, slots):
            ew, eh = fit(im.width, im.height, W * wf, low_h * 0.88)
            side_cards.append((im, ew, eh, W * xf, low_top + low_h / 2, ang))
        # Vertically centre the whole group within the region.
        group_top = hcy - hh / 2
        group_bot = max([hcy + hh / 2] + [c[4] + c[2] / 2 for c in side_cards])
        dy = (rh - (group_bot - group_top)) / 2 - (group_top - top)
        hcy += dy
        side_cards = [(a, b, c, d, e + dy, f) for (a, b, c, d, e, f) in side_cards]

    hero = card(screenshot, hw, hh, hero_radius, pal.border, border_w)

    def draw_side(sc):
        im, ew, eh, ecx, ecy, ang = sc
        place(cv, card(im, ew, eh, side_radius, pal.border, border_w), ecx, ecy, pal,
              angle=ang, shadow=int(U * 0.033), salpha=210)

    # Order mirrors the original scripts: first extra behind the hero, second on top.
    if side_cards and landscape:
        draw_side(side_cards[0])
    place(cv, hero, hcx, hcy, pal, shadow=int(U * 0.055), salpha=210, glow=glow)
    for i, sc in enumerate(side_cards):
        if landscape and i == 0:
            continue
        draw_side(sc)

    if install:
        ifont, (l, t, r, b), pw, ph, py = install
        px = int(W / 2 - pw / 2)
        layer = Image.new("RGBA", cv.size, (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        ld.rounded_rectangle((px, py, px + pw, py + ph), ph // 2, fill=rgba(pal.darker, 200),
                             outline=rgba(pal.accent, 150), width=S)
        ld.text((px + (pw - (r - l)) // 2 - l, py + (ph - (b - t)) // 2 - t), opts.install_line,
                font=ifont, fill=rgba(pal.foreground))
        cv.alpha_composite(layer)

    swatches(cv, pal.swatches, W / 2, sw_cy, sw_size, sw_gap, pal, S)
    out = cv.convert("RGB")
    return out.resize((W0, H0), Image.LANCZOS) if S > 1 else out


def resolve_backdrop(choice: str, theme_dir: Path | None, screenshot: Image.Image):
    """Return (image, description) for the backdrop choice."""
    choice = (choice or "auto").strip()
    if choice == "screenshot":
        return screenshot, "screenshot"
    if choice in ("auto", "theme"):
        bg = first_background(theme_dir)
        if bg is not None:
            try:
                return open_image(bg), str(bg)
            except RicecardError as e:
                warn(f"{e}; using the screenshot as backdrop")
        elif choice == "theme":
            warn("theme has no backgrounds/; using the screenshot as backdrop")
        return screenshot, "screenshot"
    return open_image(choice), choice


def make_cards(screenshot_path, theme_dir, out_dir, opts: Options, name: str | None = None) -> list:
    theme = Path(theme_dir).expanduser() if theme_dir else None
    if theme is not None and not theme.is_dir():
        raise RicecardError(f"theme dir not found: {theme}")
    if len(opts.extras) > 2:
        raise RicecardError("--extras takes at most 2 images")
    shot = open_image(screenshot_path)
    pal = load_palette(theme, opts.max_swatches)
    if pal.missing:
        warn("colors.toml is missing " + ", ".join(pal.missing) + " (used fallbacks)")
    bd, _ = resolve_backdrop(opts.backdrop, theme, shot)
    if bd is shot and opts.blur == Options.blur:
        # A UI screenshot needs a stronger blur to read as a backdrop.
        opts = dataclasses.replace(opts, blur=16)
    extras = [open_image(p) for p in opts.extras]
    out = Path(out_dir).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    name = slug(name) if name else theme_name(theme)
    written = []
    for (w, h) in opts.sizes:
        img = render(shot, pal, (w, h), opts, bd, extras)
        p = out / f"{name}-{w}x{h}.png"
        img.save(p, optimize=True)
        written.append(p)
    return written


# --------------------------------------------------------------------------- CLI

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ricecard",
        description="Turn a desktop screenshot + Omarchy theme into ready-to-post showcase images.")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("screenshot", nargs="?", help="screenshot image (PNG/JPG/WebP)")
    src.add_argument("--grab", action="store_true", help="take a screenshot now with grim (Wayland)")
    p.add_argument("--grab-output", metavar="NAME", help="monitor to grab (grim -o), e.g. DP-1")
    p.add_argument("--delay", type=float, default=0, help="seconds to wait before --grab")
    p.add_argument("-t", "--theme", default=DEFAULT_THEME,
                   help=f"Omarchy theme dir with colors.toml (default: {DEFAULT_THEME})")
    p.add_argument("-o", "--out", default=".", help="output directory (default: current dir)")
    p.add_argument("-n", "--name", help="output file prefix (default: theme name)")
    p.add_argument("-s", "--sizes", default=DEFAULT_SIZES,
                   help=f"comma list of WxH or presets {sorted(SIZE_PRESETS)} (default: {DEFAULT_SIZES})")
    p.add_argument("-b", "--backdrop", default="auto",
                   help="auto (theme's first background, else screenshot), theme, screenshot, or an image path")
    p.add_argument("--title", help="optional title text at the top (off by default)")
    p.add_argument("--install-line", help="optional install command shown in a pill (off by default)")
    p.add_argument("--extras", nargs="+", default=[], metavar="IMG",
                   help="up to 2 extra images shown as tilted side cards")
    p.add_argument("--swatches", type=int, default=9, help="max palette swatches (default 9)")
    p.add_argument("--no-glow", action="store_true", help="disable the accent glow behind the card")
    p.add_argument("--blur", type=float, default=10, help="backdrop blur radius (default 10)")
    p.add_argument("--dim", type=float, default=0.62, help="backdrop brightness 0..1 (default 0.62)")
    p.add_argument("--supersample", type=int, default=2, choices=[1, 2, 3, 4],
                   help="render scale for anti-aliasing (default 2)")
    p.add_argument("--font", help="TTF/OTF for --title (default: system sans bold)")
    p.add_argument("--mono-font", help="TTF/OTF for --install-line (default: system monospace)")
    p.add_argument("--version", action="version", version=f"ricecard {__version__}")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    tmp = None
    try:
        if args.grab:
            tmp = grab_screenshot(args.grab_output, args.delay)
            shot = tmp
        else:
            shot = args.screenshot
        theme = Path(args.theme).expanduser()
        if not theme.is_dir():
            if args.theme == DEFAULT_THEME:
                warn(f"no current Omarchy theme at {theme}; using fallback palette (pass --theme)")
                theme = None
            else:
                raise RicecardError(f"theme dir not found: {theme}")
        opts = Options(sizes=parse_sizes(args.sizes), backdrop=args.backdrop, title=args.title,
                       install_line=args.install_line, extras=args.extras,
                       supersample=args.supersample, glow=not args.no_glow, blur=args.blur,
                       dim=max(0.0, min(1.0, args.dim)), max_swatches=args.swatches,
                       font=args.font, mono_font=args.mono_font)
        for path in make_cards(shot, theme, args.out, opts, args.name):
            print(path)
        return 0
    except RicecardError as e:
        print(f"ricecard: error: {e}", file=sys.stderr)
        return 2
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass


if __name__ == "__main__":
    sys.exit(main())
