"""Colour parsing, brightness scaling and HSV shaping."""

from __future__ import annotations

import colorsys
import re

RGB = tuple

NAMED_COLORS = {
    "black": (0, 0, 0),
    "white": (255, 255, 255),
    "red": (255, 0, 0),
    "green": (0, 255, 0),
    "blue": (0, 0, 255),
    "cyan": (0, 255, 255),
    "magenta": (255, 0, 255),
    "yellow": (255, 255, 0),
    "orange": (255, 120, 0),
    "amber": (255, 176, 0),
    "purple": (160, 0, 255),
    "pink": (255, 105, 180),
    "teal": (0, 128, 128),
    "lime": (170, 255, 0),
    "off": (0, 0, 0),
}

_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{6})$")
_SHORT_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{3})$")
#: Saturation below which a colour's hue carries no real information.
_HUE_NOISE_FLOOR = 0.08

_TRIPLE_RE = re.compile(r"^\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*$")


class ColorError(ValueError):
    """Raised when a colour specification cannot be understood."""


def clamp(value) -> int:
    return max(0, min(255, int(value)))


def clamp_rgb(rgb) -> tuple:
    red, green, blue = rgb
    return (clamp(red), clamp(green), clamp(blue))


def to_hex(rgb) -> str:
    red, green, blue = clamp_rgb(rgb)
    return f"{red:02x}{green:02x}{blue:02x}"


def parse_color(spec, palette=None) -> tuple:
    """Parse a colour.

    Accepts ``#rrggbb``, ``rrggbb``, ``#rgb``, ``r,g,b``, a name from
    ``NAMED_COLORS``, or ``@key`` to pull ``key`` from the active theme palette.
    """
    if isinstance(spec, (tuple, list)) and len(spec) == 3:
        return clamp_rgb(spec)

    text = str(spec).strip()
    if not text:
        raise ColorError("empty colour")

    if text.startswith("@"):
        key = text[1:].strip().lower()
        if not palette:
            raise ColorError(f"'{text}' needs a theme palette but none was loaded")
        if key not in palette:
            available = ", ".join(sorted(palette)[:8])
            raise ColorError(f"'{text}' is not in the active theme (have: {available}...)")
        return clamp_rgb(palette[key])

    match = _HEX_RE.match(text)
    if match:
        value = match.group(1)
        return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))

    match = _SHORT_HEX_RE.match(text)
    if match:
        value = match.group(1)
        return tuple(int(char * 2, 16) for char in value)

    match = _TRIPLE_RE.match(text)
    if match:
        return clamp_rgb(tuple(int(group) for group in match.groups()))

    lowered = text.lower()
    if lowered in NAMED_COLORS:
        return NAMED_COLORS[lowered]

    raise ColorError(f"cannot parse colour {text!r}")


def parse_brightness(spec) -> int:
    """Parse a brightness as 0-255. Accepts ``NN`` or ``NN%``."""
    if spec is None:
        raise ColorError("empty brightness")
    text = str(spec).strip()
    if text.endswith("%"):
        try:
            percent = float(text[:-1])
        except ValueError as exc:
            raise ColorError(f"cannot parse brightness {text!r}") from exc
        return clamp(round(max(0.0, min(100.0, percent)) * 255 / 100))
    try:
        return clamp(round(float(text)))
    except ValueError as exc:
        raise ColorError(f"cannot parse brightness {text!r}") from exc


def scale(rgb, brightness: int) -> tuple:
    """Apply brightness by scaling the channels.

    Neither controller exposes a brightness field on the paths we use, so
    brightness *is* the colour: 10 of 255 is a dim ember, 255 is full output.
    """
    factor = clamp(brightness) / 255.0
    red, green, blue = clamp_rgb(rgb)
    return (round(red * factor), round(green * factor), round(blue * factor))


def boost_hsv(rgb, saturation: float = 1.0, value: float = 1.0) -> tuple:
    """Multiply saturation and value in HSV space.

    Keycaps sit behind a diffuser that visibly desaturates them, so theme
    colours usually need a saturation push to read as the colour you picked.
    """
    if saturation == 1.0 and value == 1.0:
        return clamp_rgb(rgb)
    red, green, blue = (channel / 255.0 for channel in clamp_rgb(rgb))
    hue, sat, val = colorsys.rgb_to_hsv(red, green, blue)
    sat = max(0.0, min(1.0, sat * saturation))
    val = max(0.0, min(1.0, val * value))
    red, green, blue = colorsys.hsv_to_rgb(hue, sat, val)
    return (round(red * 255), round(green * 255), round(blue * 255))


def hue_of(rgb) -> float:
    red, green, blue = (channel / 255.0 for channel in clamp_rgb(rgb))
    return colorsys.rgb_to_hsv(red, green, blue)[0]


def saturation_of(rgb) -> float:
    red, green, blue = (channel / 255.0 for channel in clamp_rgb(rgb))
    return colorsys.rgb_to_hsv(red, green, blue)[1]


def hue_distance(first, second) -> float:
    """Shortest distance between two hues on the colour wheel, 0..0.5."""
    delta = abs(hue_of(first) - hue_of(second)) % 1.0
    return min(delta, 1.0 - delta)


def complement(rgb, min_saturation: float = 0.35) -> tuple:
    """Rotate a colour's hue half-way round the wheel.

    Used to derive the far end of a gradient when the user has only picked one
    colour, so "Gradient" still produces a blend rather than a flat fill.
    """
    red, green, blue = (channel / 255.0 for channel in clamp_rgb(rgb))
    hue, sat, val = colorsys.rgb_to_hsv(red, green, blue)
    hue = (hue + 0.5) % 1.0
    sat = max(sat, min_saturation)
    val = max(val, 0.25)
    red, green, blue = colorsys.hsv_to_rgb(hue, sat, val)
    return (round(red * 255), round(green * 255), round(blue * 255))


def lift_saturation(rgb, minimum: float = 0.0) -> tuple:
    """Raise a colour's saturation to a floor, leaving richer colours alone.

    A blunt multiplier is the wrong tool here. Keycaps wash colour out, so a
    desaturated theme accent does need help - but multiplying an already-vivid
    accent just clamps it to a primary and throws the theme's character away
    (a rose red becomes pure red). Lifting only what falls short of the floor
    fixes the washed-out case and is a no-op for everything else.
    """
    if minimum <= 0.0:
        return clamp_rgb(rgb)
    red, green, blue = (channel / 255.0 for channel in clamp_rgb(rgb))
    hue, sat, val = colorsys.rgb_to_hsv(red, green, blue)
    if sat >= minimum:
        return clamp_rgb(rgb)
    # Below this, the hue is rounding noise rather than intent: a near-grey
    # #8a8588 reads as "magenta" to the maths, and lifting it would invent a
    # colour the theme never chose. A monochrome theme should light up
    # monochrome.
    if sat < _HUE_NOISE_FLOOR:
        return clamp_rgb(rgb)
    red, green, blue = colorsys.hsv_to_rgb(hue, min(1.0, minimum), val)
    return (round(red * 255), round(green * 255), round(blue * 255))
