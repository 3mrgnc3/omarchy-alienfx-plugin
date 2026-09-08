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


def test_saturation_is_exhausted_by_the_halfway_point():
    """The rate is one full step of headroom per 5 points, not per 10. Measured
    across real themes the useful travel is small - `matte-black`'s whole
    gradient has only 0.23 of saturation headroom - so half the slider was
    being spent getting somewhere it could reach in a quarter of it."""
    rgb = colors.parse_color("be3f50")
    assert colors.saturation_of(colors.apply_intensity(rgb, 5)) == pytest.approx(1.0, abs=0.01)
    assert colors.saturation_of(colors.apply_intensity(rgb, -5)) == pytest.approx(0.0, abs=0.01)


def test_the_top_half_spills_into_value_rather_than_doing_nothing():
    """Saturation caps at 1.0, so doubling the rate on its own would make
    everything past +5 identical. Once there is no white left to remove,
    further intensity raises the colour's own value instead."""
    import colorsys
    rgb = colors.parse_color("be3f50")

    def hsv(c):
        return colorsys.rgb_to_hsv(*[ch / 255 for ch in c])

    at5, at10 = hsv(colors.apply_intensity(rgb, 5)), hsv(colors.apply_intensity(rgb, 10))
    assert at10[1] == pytest.approx(at5[1], abs=0.01), "saturation should already be maxed"
    assert at10[2] > at5[2], "value must keep climbing"
    assert at10[2] == pytest.approx(1.0, abs=0.01), "+10 is as intense as the hue gets"


def test_the_negative_end_plateaus_rather_than_dimming():
    """Fully grey is fully grey. Continuing past -5 could only mean going
    darker, which is what the brightness control is for - duplicating it here
    would give two sliders that fight each other."""
    rgb = colors.parse_color("be3f50")
    assert colors.apply_intensity(rgb, -5) == colors.apply_intensity(rgb, -10)


def test_intensity_is_monotonic_across_the_whole_range():
    """Saturation must never move backwards as the slider moves right."""
    rgb = colors.parse_color("be3f50")
    sats = [colors.saturation_of(colors.apply_intensity(rgb, i)) for i in range(-10, 11)]
    assert sats == sorted(sats)


def test_every_positive_step_changes_the_colour():
    """The point of a fine-tuning slider. Between saturation up to +5 and value
    above it, all eleven positions on 0..+10 must be distinct for a colour that
    has any headroom at all."""
    rgb = colors.parse_color("be3f50")
    seen = [colors.apply_intensity(rgb, i) for i in range(0, 11)]
    assert len(set(seen)) == len(seen)


def test_intensity_never_shifts_hue():
    """Shifting hue would be a bug at any setting - it is a vibrancy control,
    not a colour picker."""
    rgb = colors.parse_color("be3f50")
    for step in range(-10, 11):
        out = colors.apply_intensity(rgb, step)
        if colors.saturation_of(out) > 0.02:      # a grey has no hue to compare
            assert colors.hue_distance(rgb, out) < 0.01, f"hue moved at {step:+d}"


def test_value_is_untouched_until_saturation_runs_out():
    """Up to +5 this is purely a saturation control, so it must not brighten
    anything - brightness has its own slider. Only once there is no white left
    to remove does it start raising value."""
    rgb = colors.parse_color("be3f50")
    for step in range(-10, 6):
        out = colors.apply_intensity(rgb, step)
        assert max(out) == pytest.approx(max(rgb), abs=1), f"value moved at {step:+d}"


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
