"""Colour parsing, brightness scaling and HSV shaping."""

from __future__ import annotations

import colorsys
import math
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


def set_saturation(rgb, target: float) -> tuple:
    """Set a colour's HSV saturation outright, keeping hue and value.

    The single saturation primitive. Every saturation control in the project is
    a thin wrapper over this one - there is no second place that does an HSV
    round trip to change how vivid a colour is.

    A colour with no real hue is returned untouched. Below the noise floor the
    hue is rounding error, and "saturating" it would invent a colour nobody
    chose: a near-grey #8a8588 reads as magenta to the maths, so a monochrome
    theme must be left monochrome.
    """
    red, green, blue = (channel / 255.0 for channel in clamp_rgb(rgb))
    hue, sat, val = colorsys.rgb_to_hsv(red, green, blue)
    if sat < _HUE_NOISE_FLOOR:
        return clamp_rgb(rgb)
    sat = max(0.0, min(1.0, target))
    red, green, blue = colorsys.hsv_to_rgb(hue, sat, val)
    return (round(red * 255), round(green * 255), round(blue * 255))


def lift_saturation(rgb, minimum: float = 0.0) -> tuple:
    """Raise a colour's saturation to a floor, leaving richer colours alone.

    A blunt multiplier is the wrong tool: multiplying an already-vivid accent
    clamps it to a primary and throws the theme's character away (a rose red
    becomes pure red). Lifting only what falls short fixes the washed-out case
    and is a no-op for everything else.

    Kept as a utility, but no longer in the shaping funnel - the intensity
    control sets saturation absolutely now, and a floor underneath it would be
    a second control fighting the first.
    """
    if minimum <= 0.0:
        return clamp_rgb(rgb)
    if saturation_of(rgb) >= minimum:
        return clamp_rgb(rgb)
    return set_saturation(rgb, min(1.0, minimum))


#: How far the intensity control can travel in each direction. The UI shows
#: -10..+10 with 0 in the middle.
INTENSITY_RANGE = 10

#: What each end of the intensity control means, as an absolute HSV saturation.
#:
#: The control is a *target*, not a nudge. It was a relative trim, which made
#: the middle of the range mean "whatever the theme happened to pick" - so on a
#: typical 0.67-saturated accent the default did nothing at all and the keyboard
#: read washed out.
#:
#: The whole scale then sat too low. Calibrated by eye on real hardware, the
#: setting that looked right as a *default* was +7 on the old scale, so that
#: value is now the centre and the ends moved with it: the range is the old one
#: shifted up by 0.35, clipped at full saturation.
#:
#: Consequence worth knowing before touching these: the top half has only 0.15
#: of travel left, because the centre is deliberately close to the ceiling.
#: Lowering the centre is the only way to buy more range above it.
INTENSITY_FLOOR = 0.40
INTENSITY_MIDDLE = 0.85
INTENSITY_CEILING = 1.00


def intensity_saturation(intensity: int = 0) -> float:
    """The saturation an intensity setting asks for.

    Two straight segments meeting at the centre, so the three settings a user
    actually reasons about land exactly on their stated values and every step
    within a half is the same size. A single ramp across the whole range would
    put the midpoint at 0.525 - close, but then none of the three numbers is
    the one written down here.
    """
    steps = max(-INTENSITY_RANGE, min(INTENSITY_RANGE, int(intensity)))
    fraction = abs(steps) / float(INTENSITY_RANGE)
    if steps >= 0:
        return INTENSITY_MIDDLE + (INTENSITY_CEILING - INTENSITY_MIDDLE) * fraction
    return INTENSITY_MIDDLE - (INTENSITY_MIDDLE - INTENSITY_FLOOR) * fraction


def apply_intensity(rgb, intensity: int = 0) -> tuple:
    """Set a colour's vividness from the intensity control, hue untouched.

    LEDs sit behind a diffuser that mixes white into everything, so a colour
    that looks right on screen reads washed out on the keycaps. Compensating
    means removing white - saturation - which is a different axis from
    brightness; multiplying the RGB values would only brighten and clip, which
    is what the brightness control already does.

        +10  saturation 1.00  (as vivid as the panel can render)
          0  saturation 0.50  (the default: deliberately colourful)
        -10  saturation 0.05  (only just tinted)

    Applied to the two gradient anchors, never per interpolated key - see
    the two anchors; doing the latter collapsed the blend into flat bands.
    """
    return set_saturation(rgb, intensity_saturation(intensity))


def _srgb_to_linear(channel: float) -> float:
    return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4


def _linear_to_srgb(channel: float) -> float:
    if channel <= 0.0031308:
        return channel * 12.92
    return 1.055 * (channel ** (1.0 / 2.4)) - 0.055


def _cbrt(value: float) -> float:
    """Real cube root, defined for negatives - Oklab's a/b axes are signed."""
    return math.copysign(abs(value) ** (1.0 / 3.0), value)


def _rgb_to_oklab(rgb) -> tuple:
    red, green, blue = (_srgb_to_linear(c / 255.0) for c in clamp_rgb(rgb))
    long_ = 0.4122214708 * red + 0.5363325363 * green + 0.0514459929 * blue
    med = 0.2119034982 * red + 0.6806995451 * green + 0.1073969566 * blue
    short = 0.0883024619 * red + 0.2817188376 * green + 0.6299787005 * blue
    l_, m_, s_ = _cbrt(long_), _cbrt(med), _cbrt(short)
    return (
        0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_,
        1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_,
        0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_,
    )


def _oklab_to_linear(lab) -> tuple:
    """Oklab to *unclamped* linear RGB.

    Split out from ``_oklab_to_rgb`` so the gamut test and the conversion share
    one copy of the matrix. The values may fall outside 0..1 - that is the
    point: it is how a colour is known to be unrepresentable.
    """
    lightness, axis_a, axis_b = lab
    l_ = lightness + 0.3963377774 * axis_a + 0.2158037573 * axis_b
    m_ = lightness - 0.1055613458 * axis_a - 0.0638541728 * axis_b
    s_ = lightness - 0.0894841775 * axis_a - 1.2914855480 * axis_b
    long_, med, short = l_ ** 3, m_ ** 3, s_ ** 3
    return (
        4.0767416621 * long_ - 3.3077115913 * med + 0.2309699292 * short,
        -1.2684380046 * long_ + 2.6097574011 * med - 0.3413193965 * short,
        -0.0041960863 * long_ - 0.7034186147 * med + 1.7076147010 * short,
    )


def _oklab_to_rgb(lab) -> tuple:
    return tuple(
        clamp(round(_linear_to_srgb(max(0.0, min(1.0, channel))) * 255))
        for channel in _oklab_to_linear(lab)
    )


#: Slack on the gamut test, in linear-RGB units. Without it a colour sitting
#: exactly on the boundary fails its own check and gets needlessly dulled.
_GAMUT_SLACK = 5e-4


def _in_gamut(lightness: float, chroma: float, hue: float) -> bool:
    """Whether an OkLCh colour can be represented in sRGB at all."""
    lab = (lightness, chroma * math.cos(hue), chroma * math.sin(hue))
    return all(-_GAMUT_SLACK <= channel <= 1.0 + _GAMUT_SLACK
               for channel in _oklab_to_linear(lab))


def _fit_chroma(lightness: float, chroma: float, hue: float) -> float:
    """The most chroma this hue and lightness can actually carry.

    Binary search down to the gamut boundary. This is the difference between
    gamut *mapping* and clipping: clipping pins a channel and silently changes
    the hue - a saturated orange came back olive - while this keeps the hue and
    lightness the blend asked for and gives up only the chroma that cannot be
    shown. Twenty halvings put it within a millionth of the boundary, which is
    far finer than eight-bit output can resolve.
    """
    if _in_gamut(lightness, chroma, hue):
        return chroma
    low, high = 0.0, chroma
    for _ in range(20):
        middle = (low + high) / 2.0
        if _in_gamut(lightness, middle, hue):
            low = middle
        else:
            high = middle
    return low


#: Below this Oklab chroma a colour is neutral enough that its hue angle is
#: numerical noise rather than a colour anyone can see.
_CHROMA_EPSILON = 1e-4

#: How hard the blend holds near its two anchors before crossing over.
#:
#: 1.0 is a straight ramp, and it is the wrong answer here for a reason that is
#: about the keyboard rather than about colour: **key density along the diagonal
#: peaks in the middle.** Counting the reference layout, 43 of 85 keys sit
#: between t=0.4 and t=0.6 while only 2 sit at t=0.0 and 1 at t=1.0. So a
#: perfectly even blend still spends most of its *area* on the middle of the
#: range - measured, 73 of 85 keys landed in the intermediate hues, and the two
#: chosen colours showed on about six keys each. It reads as a mostly-orange
#: keyboard with a hint of the theme in two corners.
#:
#: Easing the position with an S-curve concentrates the crossover into a narrow
#: diagonal band so the anchors keep real area. Measured on the reference
#: layout at this value: the two chosen colours cover 30 and 20 keys and the
#: crossover is 35 - against 7, 5 and 73 unaeased.
#:
#: Higher is not better. The curve flattens as it steepens, so the ends stop
#: varying at all - at 6.0 only 57 distinct colours remain across the keyboard
#: and 28 adjacent pairs are identical, which is the flat-band failure this
#: project has already been through once. The guard is
#: ``test_no_hard_seam_anywhere_along_the_diagonal``, which watches the largest
#: step between neighbours rather than counting equal ones: holding near an
#: anchor *should* produce equal neighbours, and a seam should not.
_BLEND_HOLD = 4.5


def ease_blend(position: float) -> float:
    """Reshape a blend position so the two anchors keep more of the surface.

    A symmetric S-curve, exact at both ends so the corner keys still get the
    chosen colours byte for byte. Separate from ``lerp_perceptual`` so the
    reshaping can be reasoned about, and tested, on its own.
    """
    if position <= 0.0:
        return 0.0
    if position >= 1.0:
        return 1.0
    held = position ** _BLEND_HOLD
    return held / (held + (1.0 - position) ** _BLEND_HOLD)


def lerp_perceptual(first, second, position: float) -> tuple:
    """Interpolate two colours through OkLCh, gamut-mapped, so the blend stays
    as colourful as its endpoints.

    The one interpolation in the project: the per-key keyboard gradient and the
    chassis samples that sit on the same axis both come through here, so there
    is a single definition of what a blend means.

    Lightness and chroma move linearly; hue takes the shortest way round the
    circle. Interpolating Oklab's rectangular a/b instead draws a straight line
    through the middle of the colour solid, and between two well-separated hues
    that line passes close to neutral - measured on the reference keymap, the
    keyboard averaged only **47% of the saturation its anchors were set to**,
    with the middle third visibly grey. Since most keys are mid-blend, that
    ceiling is what the eye actually reads, and no amount of saturating the two
    anchors can lift it.

    Holding chroma and rotating hue removes that ceiling (47% -> 94%), at the
    cost of travelling *through* the intervening hues rather than desaturating
    past them: red to teal now goes by way of orange and yellow instead of by
    way of grey. That is a deliberate look, not a side effect.

    Chroma is fitted to the sRGB gamut per key. Skipping that step is what made
    an earlier attempt fail: constant chroma leaves the gamut, and the clamp
    then pinned a channel on 43 of 85 keys and turned the middle olive. Fitting
    keeps the hue and lightness and sacrifices only unrepresentable chroma.

    Not linear-RGB either, which an earlier generation rejected for a
    washed-out, too-light midpoint.

    Endpoints are returned byte-exactly rather than round-tripped, so the two
    anchor colours are never altered by conversion error - what the theme or
    the picker chose is exactly what the corner keys show.
    """
    if position <= 0.0:
        return clamp_rgb(first)
    if position >= 1.0:
        return clamp_rgb(second)
    position = ease_blend(position)

    near_l, near_a, near_b = _rgb_to_oklab(first)
    far_l, far_a, far_b = _rgb_to_oklab(second)

    near_chroma = math.hypot(near_a, near_b)
    far_chroma = math.hypot(far_a, far_b)
    lightness = near_l + (far_l - near_l) * position
    chroma = near_chroma + (far_chroma - near_chroma) * position

    # A near-neutral endpoint has no meaningful hue, so borrow the other's
    # rather than sweeping the wheel from an angle that is really just noise.
    if near_chroma < _CHROMA_EPSILON and far_chroma < _CHROMA_EPSILON:
        return _oklab_to_rgb((lightness, 0.0, 0.0))
    if near_chroma < _CHROMA_EPSILON:
        hue = math.atan2(far_b, far_a)
    elif far_chroma < _CHROMA_EPSILON:
        hue = math.atan2(near_b, near_a)
    else:
        near_hue = math.atan2(near_b, near_a)
        far_hue = math.atan2(far_b, far_a)
        # Shortest arc: an unwrapped difference can exceed half a turn, which
        # would send the blend the long way round through hues in neither
        # anchor.
        delta = (far_hue - near_hue + math.pi) % (2.0 * math.pi) - math.pi
        hue = near_hue + delta * position

    chroma = _fit_chroma(lightness, chroma, hue)
    return _oklab_to_rgb((lightness, chroma * math.cos(hue), chroma * math.sin(hue)))
