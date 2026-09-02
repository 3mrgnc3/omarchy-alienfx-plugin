"""Reading the active Omarchy theme palette.

Omarchy 4 keeps the active theme as a symlink under
``~/.local/state/omarchy/current/theme``.  Earlier layouts used
``~/.config/omarchy/current/theme``, so both are tried in order.

The palette itself is the theme's ``colors.toml``.  Real themes use *named*
keys - ``accent``, ``foreground``, ``red``, ``cyan`` and so on - not the
``color0..color15`` scheme, so anything that assumes numbered keys will not
resolve on a stock Omarchy theme.
"""

from __future__ import annotations

import colorsys
import os
import re

from . import colors

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
    tomllib = None

_THEME_DIRS = (
    "~/.local/state/omarchy/current/theme",
    "~/.config/omarchy/current/theme",
)
_THEME_NAMES = (
    "~/.local/state/omarchy/current/theme.name",
    "~/.config/omarchy/current/theme.name",
)

_HEX_VALUE_RE = re.compile(r"^#?([0-9a-fA-F]{6}|[0-9a-fA-F]{3})$")

#: Hue distance (0..0.5) below which two colours cannot make a visible
#: gradient - only a band. See auto_secondary for why this has to exist.
_MIN_USEFUL_HUE_DISTANCE = 0.12

#: Palette keys worth considering as the far end of a gradient.
#:
#: Deliberately excludes foreground/background so the blend stays chromatic, and
#: excludes `selection`/`muted` because those are UI chrome rather than palette
#: colours - they are usually dark greys, and on some themes one of them is the
#: most hue-distant entry, which produced a "gradient" that just faded to black.
_SECONDARY_CANDIDATES = (
    "cyan", "magenta", "blue", "green", "red", "yellow", "orange",
    "bright_cyan", "bright_magenta", "bright_blue",
    "bright_green", "bright_red", "bright_yellow",
)


class PaletteError(RuntimeError):
    """Raised when the active theme palette cannot be read."""


def theme_dir():
    """Return the active theme directory, or None if no theme is resolvable."""
    for candidate in _THEME_DIRS:
        path = os.path.expanduser(candidate)
        if os.path.isdir(path):
            return os.path.realpath(path)
    return None


def theme_name() -> str:
    for candidate in _THEME_NAMES:
        path = os.path.expanduser(candidate)
        try:
            with open(path, "r", encoding="utf-8") as handle:
                name = handle.read().strip()
            if name:
                return name
        except OSError:
            continue
    directory = theme_dir()
    return os.path.basename(directory) if directory else "unknown"


def colors_path():
    directory = theme_dir()
    return os.path.join(directory, "colors.toml") if directory else None


def load_palette() -> dict:
    """Return ``{key: (r, g, b)}`` for every colour the active theme defines.

    Non-colour values (``mode = "dark"``, the ``rgba(...)`` Hyprland border
    strings) are skipped rather than raising, so a theme carrying extra keys
    still loads.
    """
    path = colors_path()
    if not path or not os.path.isfile(path):
        raise PaletteError("no active Omarchy theme palette found")
    if tomllib is None:
        raise PaletteError("Python 3.11+ is required to read the theme palette")

    try:
        with open(path, "rb") as handle:
            raw = tomllib.load(handle)
    except OSError as exc:
        raise PaletteError(f"{path}: {exc}") from exc
    except Exception as exc:
        raise PaletteError(f"{path}: malformed TOML ({exc})") from exc

    palette = {}
    for key, value in raw.items():
        if not isinstance(value, str):
            continue
        if _HEX_VALUE_RE.match(value.strip()):
            try:
                palette[key.strip().lower()] = colors.parse_color(value.strip())
            except colors.ColorError:
                continue
    if not palette:
        raise PaletteError(f"{path}: no usable colours")
    return palette


def keyboard_rgb():
    """Return the theme's ``keyboard.rgb`` colour, if it ships one.

    Omarchy preserves this file on every theme but never reads it, so it is a
    free per-theme hook.  Accepts the value with or without a leading ``#``:
    stock themes are inconsistent about it.
    """
    directory = theme_dir()
    if not directory:
        return None
    path = os.path.join(directory, "keyboard.rgb")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read().strip()
    except OSError:
        return None
    if not text:
        return None
    try:
        return colors.parse_color(text)
    except colors.ColorError:
        return None


def primary_of(palette) -> tuple:
    """The near end of the gradient: the theme's accent, else its foreground."""
    for key in ("accent", "bright_blue", "blue", "foreground"):
        if key in palette:
            return palette[key]
    return next(iter(palette.values()))


def auto_secondary(palette, primary) -> tuple:
    """Pick the far end of the gradient automatically.

    Chooses the most hue-distant reasonably saturated colour in the theme, so
    the diagonal reads as a real blend on any palette rather than two shades of
    the same hue.  Monochrome themes have nothing distant to offer, so we
    rotate the accent's hue instead and keep a visible gradient.
    """
    best, best_distance = None, -1.0
    for key in _SECONDARY_CANDIDATES:
        candidate = palette.get(key)
        if candidate is None:
            continue
        if colors.saturation_of(candidate) < 0.15:
            continue
        distance = colors.hue_distance(primary, candidate)
        if distance > best_distance:
            best, best_distance = candidate, distance

    # A theme with nothing distant enough to offer cannot produce a gradient,
    # only a band of one colour. The archived implementation got this right by
    # accident: its far anchor defaulted to a palette ref (`@color5`) that never
    # resolves on an Omarchy theme, so it always fell back to a fixed periwinkle
    # and always had a wide, obvious blend. Deriving from the theme is better
    # when the theme has something to give, and has to fall back when it does
    # not - `matte-black` defines every colour key as an amber, a red or a grey,
    # and its most distant entry is 0.099 away, which reads as a flat fill.
    if best is None or best_distance < _MIN_USEFUL_HUE_DISTANCE:
        return colors.match_value(colors.complement(primary), primary)

    # Take the hue, not the brightness. A dark palette entry used raw makes the
    # gradient fade towards black instead of travelling through colour.
    return colors.match_value(best, primary)


def anchors():
    """Return the (primary, secondary) gradient anchors for the active theme."""
    palette = load_palette()
    primary = primary_of(palette)
    return primary, auto_secondary(palette, primary)
