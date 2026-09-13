"""Theme palette selection.

Characterisation tests. `palette.py` decides both ends of the ThemeSync
gradient, which is the feature this plugin exists for, and it had no tests at
all - while also being the module we agreed is settled and should not be
changed. Untested *and* off-limits is the worst pairing: nobody can safely
touch it, and nothing would notice if they did.

So these pin what it does today rather than arguing about what it should do.
`tests/data/theme-palettes.json` holds the selection-relevant colours of the
Omarchy themes installed on the development machine, plus the anchors this code
picked for each. If a change moves any of them, that is a decision to make
deliberately, not a surprise to discover on the keyboard.

One earlier attempt to "improve" this - raising the saturation threshold and
borrowing the accent's brightness for the chosen hue - was reverted for
inventing colours the theme never specified.
"""

import json
import os

import pytest

from alienfx_ctl import colors, palette

_FIXTURE = os.path.join(os.path.dirname(__file__), "data", "theme-palettes.json")


def _themes():
    with open(_FIXTURE, encoding="utf-8") as handle:
        return json.load(handle)


THEMES = _themes()
NAMES = sorted(THEMES)


def _palette_of(name):
    return {key: tuple(value) for key, value in THEMES[name]["palette"].items()}


# --------------------------------------------------------- characterisation

@pytest.mark.parametrize("name", NAMES)
def test_the_near_anchor_is_unchanged_for_every_theme(name):
    assert palette.primary_of(_palette_of(name)) == tuple(THEMES[name]["primary"])


@pytest.mark.parametrize("name", NAMES)
def test_the_far_anchor_is_unchanged_for_every_theme(name):
    pal = _palette_of(name)
    primary = tuple(THEMES[name]["primary"])
    assert palette.auto_secondary(pal, primary) == tuple(THEMES[name]["secondary"])


@pytest.mark.parametrize("name", NAMES)
def test_both_anchors_are_real_colours(name):
    for anchor in (THEMES[name]["primary"], THEMES[name]["secondary"]):
        assert len(anchor) == 3
        assert all(isinstance(channel, int) and 0 <= channel <= 255
                   for channel in anchor)


@pytest.mark.parametrize("name", NAMES)
def test_the_two_anchors_always_differ(name):
    """Identical anchors would render a flat fill and silently turn ThemeSync
    into Solid. Every installed theme yields two distinct colours."""
    assert tuple(THEMES[name]["primary"]) != tuple(THEMES[name]["secondary"])


def test_the_fixture_covers_a_useful_spread_of_themes():
    """A guard on the guard: if the fixture shrinks to a couple of themes the
    parametrised tests above stop meaning very much."""
    assert len(NAMES) >= 20


# ----------------------------------------------------------- known weak spots
#
# Recorded, not fixed. These are the cases an "improvement" would target, so
# they are written down to make the trade-off visible.

def test_some_themes_legitimately_yield_a_dark_far_anchor():
    """`hackerman` picks #2d3450 (value 0.31) against a bright green accent, so
    its gradient fades towards near-black. That is a genuine property of the
    theme, and a blend into a dark colour is a fair rendering of it. The fix
    that borrowed the accent's brightness was reverted for synthesising colours
    the theme never specified."""
    pal = _palette_of("hackerman")
    primary = tuple(THEMES["hackerman"]["primary"])
    secondary = palette.auto_secondary(pal, primary)
    assert max(secondary) / 255.0 < 0.4, "still the dark pick"


def test_a_low_contrast_theme_still_produces_two_hues():
    """`miasma` offers nothing far from its accent, so the anchors end up close
    together and the keyboard reads nearly flat. Correct, but worth knowing
    before blaming the renderer."""
    pal = _palette_of("miasma")
    primary = tuple(THEMES["miasma"]["primary"])
    secondary = palette.auto_secondary(pal, primary)
    assert colors.hue_distance(primary, secondary) < 0.2


@pytest.mark.parametrize("name", ["vantablack", "white"])
def test_a_monochrome_theme_gets_a_synthesised_complement(name):
    """With no saturated candidate to travel to, the accent's hue is rotated
    and its saturation floored so there is still a visible gradient rather than
    two shades of grey."""
    if name not in THEMES:
        pytest.skip(f"{name} not in the fixture")
    pal = _palette_of(name)
    primary = tuple(THEMES[name]["primary"])
    secondary = palette.auto_secondary(pal, primary)
    assert secondary != primary
    assert colors.saturation_of(secondary) >= 0.3, "the floor was applied"


# ------------------------------------------------------------- pure behaviour

def test_the_accent_wins_over_everything_else():
    pal = {"accent": (1, 2, 3), "blue": (4, 5, 6), "foreground": (7, 8, 9)}
    assert palette.primary_of(pal) == (1, 2, 3)


def test_the_fallback_order_is_accent_then_blues_then_foreground():
    assert palette.primary_of({"bright_blue": (1, 1, 1), "blue": (2, 2, 2)}) == (1, 1, 1)
    assert palette.primary_of({"blue": (2, 2, 2), "foreground": (3, 3, 3)}) == (2, 2, 2)
    assert palette.primary_of({"foreground": (3, 3, 3)}) == (3, 3, 3)


def test_an_unfamiliar_palette_still_yields_something():
    """A theme with none of the expected keys must not crash ThemeSync."""
    assert palette.primary_of({"surprise": (9, 9, 9)}) == (9, 9, 9)


def test_washed_out_candidates_are_not_considered():
    """Below 0.15 saturation a candidate is grey, and travelling to grey is not
    a gradient. Such a palette falls through to the synthesised complement."""
    pal = {"accent": (190, 63, 80), "cyan": (128, 127, 129)}
    secondary = palette.auto_secondary(pal, (190, 63, 80))
    assert secondary != (128, 127, 129)


def test_a_candidate_too_close_in_hue_is_rejected():
    """Under 0.06 hue distance the "blend" would be two shades of one colour."""
    pal = {"accent": (190, 63, 80), "red": (200, 70, 88)}
    secondary = palette.auto_secondary(pal, (190, 63, 80))
    assert secondary != (200, 70, 88)


def test_the_most_distant_candidate_wins_not_the_first():
    pal = {"accent": (255, 0, 0), "yellow": (255, 200, 0), "cyan": (0, 255, 255)}
    assert palette.auto_secondary(pal, (255, 0, 0)) == (0, 255, 255)


# ----------------------------------------------------------------- the loader

def test_non_colour_values_are_skipped(tmp_path, monkeypatch):
    """Themes carry `mode = "dark"` and Hyprland `rgba(...)` border strings.
    A theme with extra keys must still load."""
    toml = tmp_path / "colors.toml"
    toml.write_text('accent = "#be3f50"\nmode = "dark"\n'
                    'border = "rgba(be3f50ff)"\ncyan = "#3fbead"\n')
    monkeypatch.setattr(palette, "colors_path", lambda: str(toml))
    loaded = palette.load_palette()
    assert loaded == {"accent": (190, 63, 80), "cyan": (63, 190, 173)}


def test_three_digit_hex_and_a_missing_hash_both_load(tmp_path, monkeypatch):
    """Stock themes are inconsistent about the leading #."""
    toml = tmp_path / "colors.toml"
    toml.write_text('accent = "f00"\ncyan = "3fbead"\n')
    monkeypatch.setattr(palette, "colors_path", lambda: str(toml))
    assert palette.load_palette() == {"accent": (255, 0, 0), "cyan": (63, 190, 173)}


def test_a_palette_with_no_usable_colours_is_an_error(tmp_path, monkeypatch):
    toml = tmp_path / "colors.toml"
    toml.write_text('mode = "dark"\n')
    monkeypatch.setattr(palette, "colors_path", lambda: str(toml))
    with pytest.raises(palette.PaletteError):
        palette.load_palette()


def test_malformed_toml_is_an_error_not_a_traceback(tmp_path, monkeypatch):
    toml = tmp_path / "colors.toml"
    toml.write_text("this is not = = toml\n")
    monkeypatch.setattr(palette, "colors_path", lambda: str(toml))
    with pytest.raises(palette.PaletteError):
        palette.load_palette()


def test_no_active_theme_is_an_error(monkeypatch):
    monkeypatch.setattr(palette, "colors_path", lambda: None)
    with pytest.raises(palette.PaletteError):
        palette.load_palette()


def test_keyboard_rgb_is_absent_without_a_theme(monkeypatch):
    monkeypatch.setattr(palette, "theme_dir", lambda: None)
    assert palette.keyboard_rgb() is None


def test_keyboard_rgb_reads_the_per_theme_hook(tmp_path, monkeypatch):
    """Omarchy preserves this file on every theme but never reads it, so it is
    a free per-theme override."""
    (tmp_path / "keyboard.rgb").write_text("#3fbead\n")
    monkeypatch.setattr(palette, "theme_dir", lambda: str(tmp_path))
    assert palette.keyboard_rgb() == (63, 190, 173)


def test_a_junk_keyboard_rgb_is_ignored_rather_than_fatal(tmp_path, monkeypatch):
    (tmp_path / "keyboard.rgb").write_text("not a colour\n")
    monkeypatch.setattr(palette, "theme_dir", lambda: str(tmp_path))
    assert palette.keyboard_rgb() is None
