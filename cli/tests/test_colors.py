"""Colour parsing, brightness and shaping."""

import pytest

from alienfx_ctl import colors


@pytest.mark.parametrize("spec,expected", [
    ("ff7800", (255, 120, 0)),
    ("#ff7800", (255, 120, 0)),
    ("#f70", (255, 119, 0)),
    ("255,120,0", (255, 120, 0)),
    (" 255 , 120 , 0 ", (255, 120, 0)),
    ("orange", (255, 120, 0)),
    ("WHITE", (255, 255, 255)),
    ((1, 2, 3), (1, 2, 3)),
])
def test_parse_color_forms(spec, expected):
    assert colors.parse_color(spec) == expected


def test_parse_color_palette_ref():
    palette = {"accent": (10, 20, 30)}
    assert colors.parse_color("@accent", palette=palette) == (10, 20, 30)


def test_palette_ref_without_palette_is_an_error():
    with pytest.raises(colors.ColorError):
        colors.parse_color("@accent")


def test_unknown_palette_key_is_an_error():
    with pytest.raises(colors.ColorError):
        colors.parse_color("@nope", palette={"accent": (0, 0, 0)})


@pytest.mark.parametrize("bad", ["", "zzz", "300,0", "#12345", "not a colour"])
def test_parse_color_rejects_junk(bad):
    with pytest.raises(colors.ColorError):
        colors.parse_color(bad)


def test_channels_are_clamped_not_wrapped():
    assert colors.parse_color("999,120,0") == (255, 120, 0)


@pytest.mark.parametrize("spec,expected", [
    ("0", 0), ("255", 255), ("26", 26),
    ("10%", 26), ("0%", 0), ("100%", 255),
    ("500", 255), ("-5", 0), ("150%", 255),
])
def test_parse_brightness(spec, expected):
    assert colors.parse_brightness(spec) == expected


def test_scale_is_proportional():
    assert colors.scale((255, 120, 0), 255) == (255, 120, 0)
    assert colors.scale((255, 120, 0), 0) == (0, 0, 0)
    # 10% of full output, which is the ambient default this tool ships with.
    assert colors.scale((200, 100, 0), 26) == (20, 10, 0)


def test_hex_round_trip():
    for spec in ("ff7800", "000000", "ffffff", "b59790"):
        assert colors.to_hex(colors.parse_color(spec)) == spec


def test_boost_hsv_is_identity_at_one():
    assert colors.boost_hsv((123, 45, 67), 1.0, 1.0) == (123, 45, 67)


def test_boost_hsv_raises_saturation():
    """A washed-out theme colour should get more chromatic, not brighter."""
    before = (181, 151, 144)
    after = colors.boost_hsv(before, 2.0, 1.0)
    assert colors.saturation_of(after) > colors.saturation_of(before)
    assert max(after) == pytest.approx(max(before), abs=1)


def test_boost_hsv_saturation_cannot_exceed_full():
    assert colors.boost_hsv((255, 0, 0), 10.0, 1.0) == (255, 0, 0)


def test_complement_is_roughly_opposite():
    assert colors.hue_distance((255, 0, 0), colors.complement((255, 0, 0))) == pytest.approx(0.5, abs=0.01)


def test_complement_of_grey_is_still_saturated():
    """A grey has no hue to oppose, so the complement must invent chroma or
    'Gradient' would render as a flat fill."""
    assert colors.saturation_of(colors.complement((128, 128, 128))) >= 0.3


def test_hue_distance_wraps_the_colour_wheel():
    assert colors.hue_distance((255, 0, 0), (255, 0, 1)) < 0.01


def test_lift_saturation_leaves_a_vivid_colour_untouched():
    """Multiplying an already-vivid accent clamps it to a primary and throws
    the theme's character away, so the floor must be a no-op here."""
    vivid = colors.parse_color("be3f50")
    assert colors.lift_saturation(vivid, 0.55) == vivid


def test_lift_saturation_rescues_a_washed_out_colour():
    washed = colors.parse_color("b59790")
    lifted = colors.lift_saturation(washed, 0.55)
    assert colors.saturation_of(lifted) == pytest.approx(0.55, abs=0.02)
    # The hue must survive the lift; only the saturation changes.
    assert colors.hue_distance(washed, lifted) < 0.02


def test_lift_saturation_keeps_near_greys_neutral():
    """At saturation 0.04 the hue is rounding noise. Lifting it would invent a
    colour the theme never chose, so a monochrome theme stays monochrome."""
    grey = colors.parse_color("8a8588")
    assert colors.lift_saturation(grey, 0.55) == grey


def test_lift_saturation_of_pure_grey_is_identity():
    assert colors.lift_saturation((128, 128, 128), 0.9) == (128, 128, 128)


def test_lift_saturation_zero_floor_is_identity():
    washed = colors.parse_color("b59790")
    assert colors.lift_saturation(washed, 0.0) == washed


def test_lift_saturation_preserves_value():
    washed = colors.parse_color("b59790")
    lifted = colors.lift_saturation(washed, 0.55)
    assert max(lifted) == pytest.approx(max(washed), abs=1)


# --------------------------------------------------------- intensity trim
#
# LEDs sit behind a diffuser that mixes white in, so a colour that looks right
# on screen reads washed out on the keycaps. The trim removes white (raises
# saturation) rather than multiplying RGB, which would only brighten and clip.

def test_intensity_zero_is_an_exact_no_op():
    """The default must reproduce the previous output byte for byte, or every
    existing install changes appearance on upgrade."""
    for spec in ("be3f50", "82fb9c", "e68e0d", "000000", "ffffff", "808080"):
        rgb = colors.parse_color(spec)
        assert colors.apply_intensity(rgb, 0) == rgb


def test_positive_intensity_raises_saturation():
    rgb = colors.parse_color("be3f50")
    before = colors.saturation_of(rgb)
    after = colors.saturation_of(colors.apply_intensity(rgb, 5))
    assert after > before


def test_negative_intensity_lowers_saturation():
    rgb = colors.parse_color("be3f50")
    assert colors.saturation_of(colors.apply_intensity(rgb, -5)) < colors.saturation_of(rgb)


def test_the_ends_of_the_range_are_meaningful():
    """A multiplier was the obvious first choice and clamps at full saturation,
    so with typical theme colours everything past about +5 looked identical.
    Moving a fraction of the remaining headroom makes both ends land exactly."""
    rgb = colors.parse_color("be3f50")
    assert colors.saturation_of(colors.apply_intensity(rgb, 10)) == pytest.approx(1.0, abs=0.01)
    assert colors.saturation_of(colors.apply_intensity(rgb, -10)) == pytest.approx(0.0, abs=0.01)


def test_intensity_is_monotonic_across_the_whole_range():
    """Every step has to change something, in the right direction - that is the
    whole point of a fine-tuning slider."""
    rgb = colors.parse_color("be3f50")
    sats = [colors.saturation_of(colors.apply_intensity(rgb, i)) for i in range(-10, 11)]
    assert sats == sorted(sats)
    assert len(set(round(s, 3) for s in sats)) == len(sats), "some steps do nothing"


def test_intensity_preserves_hue_and_brightness():
    """It is a vibrancy control, not a brightness or colour control - brightness
    already has its own slider, and shifting hue would be a bug."""
    rgb = colors.parse_color("be3f50")
    for step in (-8, -4, 4, 8, 10):
        out = colors.apply_intensity(rgb, step)
        assert max(out) == pytest.approx(max(rgb), abs=1), "brightness moved"
        assert colors.hue_distance(rgb, out) < 0.01, "hue moved"


def test_intensity_leaves_a_neutral_grey_alone():
    """Below the hue noise floor the hue is rounding error; 'saturating' a grey
    would invent a colour the user never chose - it would come out red."""
    for spec in ("808080", "ffffff", "000000", "8a8588"):
        rgb = colors.parse_color(spec)
        assert colors.apply_intensity(rgb, 10) == rgb
        assert colors.apply_intensity(rgb, -10) == rgb


@pytest.mark.parametrize("given,expected", [(50, 10), (-50, -10), (11, 10), (-11, -10)])
def test_intensity_is_clamped_to_its_range(given, expected):
    rgb = colors.parse_color("be3f50")
    assert colors.apply_intensity(rgb, given) == colors.apply_intensity(rgb, expected)
