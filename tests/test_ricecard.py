from pathlib import Path

import pytest
from PIL import Image

import ricecard as rc


# ------------------------------------------------------------------ palette

def test_theme_palette_reads_real_values(theme):
    raw = rc.read_colors_toml(theme / "colors.toml")
    pal = rc.load_palette(theme)
    assert pal.accent == rc.parse_color(raw["accent"])
    assert pal.background == rc.parse_color(raw["background"])
    assert pal.darker == rc.parse_color(raw["darker_background"])
    assert pal.swatches[0] == pal.accent
    assert pal.swatches[-1] == pal.foreground
    assert 3 <= len(pal.swatches) <= 9
    assert pal.missing == []


def test_swatches_are_deduplicated():
    pal = rc.Palette.from_mapping({"accent": "#ff0000", "red": "#ff0000", "blue": "#0000ff"})
    assert pal.swatches.count((255, 0, 0)) == 1


@pytest.mark.parametrize("value,expected", [
    ("#c1121f", (0xc1, 0x12, 0x1f)),
    ("c1121f", (0xc1, 0x12, 0x1f)),
    ("#abc", (0xaa, 0xbb, 0xcc)),
    ("#c1121fee", (0xc1, 0x12, 0x1f)),
    ("0xc1121f", (0xc1, 0x12, 0x1f)),
    ("rgba(c1121fee)", (0xc1, 0x12, 0x1f)),
    ("rgba(ff4fa3ee) rgba(4fe3f0ee) 45deg", (0xff, 0x4f, 0xa3)),
    ("rgb(10, 20, 30)", (10, 20, 30)),
    ("nonsense", None),
    ("#12345", None),
    (42, None),
])
def test_parse_color(value, expected):
    assert rc.parse_color(value) == expected


def test_missing_keys_fall_back(tmp_path):
    (tmp_path / "colors.toml").write_text('accent = "#ff4fa3"\n')
    pal = rc.load_palette(tmp_path)
    assert pal.accent == (0xff, 0x4f, 0xa3)
    assert pal.background == rc.parse_color(rc.FALLBACK["background"])
    assert "background" in pal.missing and "foreground" in pal.missing
    assert len(pal.swatches) >= 2


def test_colorN_aliases_used(tmp_path):
    (tmp_path / "colors.toml").write_text(
        'color0 = "#101010"\ncolor1 = "#ff0000"\ncolor2 = "#00ff00"\ncolor4 = "#0000ff"\ncolor7 = "#eeeeee"\n')
    pal = rc.load_palette(tmp_path)
    assert pal.background == (16, 16, 16)
    assert pal.foreground == (238, 238, 238)
    assert (255, 0, 0) in pal.swatches and (0, 0, 255) in pal.swatches


def test_empty_bad_and_missing_colors_toml(tmp_path, capsys):
    (tmp_path / "colors.toml").write_text("")
    assert rc.load_palette(tmp_path).swatches
    (tmp_path / "colors.toml").write_text('this is = = not toml\naccent = "#123456"\nred = "zzz"\n')
    pal = rc.load_palette(tmp_path)
    assert pal.accent == (0x12, 0x34, 0x56)
    (tmp_path / "colors.toml").unlink()
    assert rc.load_palette(tmp_path).swatches
    assert rc.load_palette(None).swatches
    assert "warning" in capsys.readouterr().err


def test_light_theme_without_foreground(tmp_path):
    (tmp_path / "colors.toml").write_text('background = "#fafafa"\n')
    pal = rc.load_palette(tmp_path)
    assert rc.luminance(pal.foreground) < 0.5


# ------------------------------------------------------------------ helpers

def test_parse_sizes():
    assert rc.parse_sizes("1600x900,1200x1200") == [(1600, 900), (1200, 1200)]
    assert rc.parse_sizes("wide square 1600x900") == [(1600, 900), (1200, 1200)]
    assert rc.parse_sizes("1080X1920") == [(1080, 1920)]
    for bad in ("", "big", "10x10", "1600x"):
        with pytest.raises(rc.RicecardError):
            rc.parse_sizes(bad)


def test_first_background(theme):
    bg = rc.first_background(theme)
    assert bg is not None and bg.parent.name == "backgrounds"
    assert bg == sorted((theme / "backgrounds").glob("*.png"))[0]


def test_theme_name(theme, tmp_path):
    assert rc.theme_name(theme) == theme.name
    cur = tmp_path / "current"
    cur.mkdir()
    (cur / "theme").symlink_to(theme)
    assert rc.theme_name(cur / "theme") == theme.name
    plain = cur / "copied" / "theme"
    plain.mkdir(parents=True)
    (cur / "copied" / "theme.name").write_text("My Theme\n")
    assert rc.theme_name(plain) == "my-theme"


# ------------------------------------------------------------------ rendering

def _opts(**kw):
    kw.setdefault("supersample", 1)
    return rc.Options(**kw)


@pytest.mark.parametrize("size", [(400, 225), (300, 300), (270, 480), (480, 160)])
def test_render_sizes(theme, size):
    shot = rc.open_image(theme / "preview.png")
    pal = rc.load_palette(theme)
    img = rc.render(shot, pal, size, _opts(), rc.open_image(rc.first_background(theme)))
    assert img.size == size and img.mode == "RGB"


def test_render_with_extras_and_text(theme):
    shot = rc.open_image(theme / "preview.png")
    pal = rc.load_palette(theme)
    extras = [rc.open_image(p) for p in sorted((theme / "screenshots").glob("*.png"))[:2]]
    for size in [(480, 270), (360, 360), (270, 480)]:
        for ex in (extras[:1], extras):
            img = rc.render(shot, pal, size, _opts(title="Theme", install_line="omarchy-theme-install x"),
                            None, ex)
            assert img.size == size


def test_default_has_no_text(theme):
    """Title/install line are opt-in: the default top band must equal the no-title render."""
    shot = rc.open_image(theme / "preview.png")
    pal = rc.load_palette(theme)
    a = rc.render(shot, pal, (400, 400), _opts())
    b = rc.render(shot, pal, (400, 400), _opts(title=None, install_line=None))
    c = rc.render(shot, pal, (400, 400), _opts(title="HELLO"))
    assert a.tobytes() == b.tobytes()
    assert a.tobytes() != c.tobytes()


def test_swatch_colors_present(theme):
    """Each swatch colour is painted somewhere on the output (sampled at dot centres)."""
    shot = rc.open_image(theme / "preview.png")
    pal = rc.load_palette(theme)
    img = rc.render(shot, pal, (800, 450), _opts())
    px = [c for _, c in img.getcolors(maxcolors=1 << 24)]
    for c in pal.swatches:
        assert any(sum(abs(p[i] - c[i]) for i in range(3)) <= 12 for p in px), rc.to_hex(c)


def test_backdrop_choices(theme, tmp_path):
    shot = rc.open_image(theme / "preview.png")
    img, desc = rc.resolve_backdrop("auto", theme, shot)
    assert desc.endswith(".png") and "backgrounds" in desc
    assert rc.resolve_backdrop("screenshot", theme, shot)[0] is shot
    assert rc.resolve_backdrop("auto", tmp_path, shot)[0] is shot  # no backgrounds/
    with pytest.raises(rc.RicecardError):
        rc.resolve_backdrop(str(tmp_path / "nope.png"), theme, shot)


# ------------------------------------------------------------------ CLI / end-to-end

def test_cli_default_sizes(theme, tmp_path):
    rc_ = rc.main([str(theme / "preview.png"), "--theme", str(theme), "--out", str(tmp_path)])
    assert rc_ == 0
    for w, h in [(1600, 900), (1200, 1200)]:
        p = tmp_path / f"{theme.name}-{w}x{h}.png"
        assert p.is_file()
        with Image.open(p) as im:
            assert im.size == (w, h)


def test_cli_custom_sizes_extras_backdrop(theme, tmp_path, capsys):
    extras = [str(p) for p in sorted((theme / "screenshots").glob("*.png"))[:2]]
    code = rc.main([str(theme / "preview.png"), "-t", str(theme), "-o", str(tmp_path), "-n", "x",
                    "--sizes", "640x360,og", "--backdrop", "screenshot", "--supersample", "1",
                    "--title", "T", "--install-line", "install", "--extras", *extras, "--no-glow"])
    assert code == 0
    out = capsys.readouterr().out.split()
    assert [Path(p).name for p in out] == ["x-640x360.png", "x-1200x630.png"]


def test_cli_errors(theme, tmp_path, capsys):
    shot = str(theme / "preview.png")
    assert rc.main([shot, "-t", str(tmp_path / "missing")]) == 2
    assert rc.main([str(tmp_path / "missing.png"), "-t", str(theme)]) == 2
    assert rc.main([shot, "-t", str(theme), "-o", str(tmp_path), "--extras", shot, shot, shot]) == 2
    assert rc.main([shot, "-t", str(theme), "-o", str(tmp_path), "--sizes", "huge"]) == 2
    assert "error" in capsys.readouterr().err


def test_cli_needs_input():
    with pytest.raises(SystemExit):
        rc.main([])


def test_grab_without_wayland(monkeypatch):
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    with pytest.raises(rc.RicecardError):
        rc.grab_screenshot()
