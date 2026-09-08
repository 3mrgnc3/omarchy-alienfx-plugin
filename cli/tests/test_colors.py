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

def test_intensity_zero_is_a_deliberate_and_vivid_default():
    """The centre of the control is a *known* value, not "whatever the theme
    happened to pick". It used to be an exact no-op, which sounded safe but
    meant the default did nothing at all: theme accents arrive around 0.67
    saturation and read washed out through the diffuser.

    It sits close to the ceiling because that is where it was calibrated by eye
    on real hardware - deliberately vivid, with the range's remaining travel
    spent below it rather than above."""
    for spec in ("be3f50", "82fb9c", "e68e0d"):
        out = colors.apply_intensity(colors.parse_color(spec), 0)
        assert colors.saturation_of(out) == pytest.approx(colors.INTENSITY_MIDDLE, abs=0.01)
    assert colors.INTENSITY_MIDDLE > 0.8, "the default must not be pale"


def test_colours_with_no_hue_are_left_alone_at_every_setting():
    """Saturating a grey would invent a colour nobody chose - below the noise
    floor its hue is rounding error, so a monochrome theme must stay
    monochrome."""
    for spec in ("000000", "ffffff", "808080"):
        rgb = colors.parse_color(spec)
        for step in (-10, 0, 10):
            assert colors.apply_intensity(rgb, step) == rgb


def test_positive_intensity_raises_saturation():
    rgb = colors.parse_color("be3f50")
    before = colors.saturation_of(rgb)
    after = colors.saturation_of(colors.apply_intensity(rgb, 5))
    assert after > before


def test_negative_intensity_lowers_saturation():
    rgb = colors.parse_color("be3f50")
    assert colors.saturation_of(colors.apply_intensity(rgb, -5)) < colors.saturation_of(rgb)


def test_the_three_settings_that_matter_land_on_their_stated_values():
    """-10, 0 and +10 are the settings a user reasons about, so they hit the
    documented numbers exactly rather than approximately."""
    assert colors.intensity_saturation(-10) == pytest.approx(colors.INTENSITY_FLOOR)
    assert colors.intensity_saturation(0) == pytest.approx(colors.INTENSITY_MIDDLE)
    assert colors.intensity_saturation(10) == pytest.approx(colors.INTENSITY_CEILING)


def test_the_floor_is_muted_but_still_clearly_coloured():
    """The bottom of the range is a muted setting, not a colourless one. It was
    briefly near-white (0.05), which turned out to be further than anyone wanted
    to go - the whole scale was recalibrated upwards, and the bottom with it."""
    assert 0.3 < colors.INTENSITY_FLOOR < 0.5
    out = colors.apply_intensity(colors.parse_color("be3f50"), -10)
    assert max(out) - min(out) > 40, "still obviously a colour"
    assert colors.saturation_of(out) < colors.INTENSITY_MIDDLE, "and muted"


def test_each_half_of_the_range_is_evenly_spaced():
    """Two straight segments meeting at the centre, so a step feels the same
    size wherever the user is on that half."""
    for half in (range(-10, 1), range(0, 11)):
        steps = [colors.intensity_saturation(i) for i in half]
        gaps = [b - a for a, b in zip(steps, steps[1:])]
        assert max(gaps) - min(gaps) < 1e-9, "uneven steps"


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
        # Hue is only meaningful once there is some colour to measure it on.
        # At the 0.05 saturation of the bottom end the colour is (190,180,182),
        # where one unit of eight-bit rounding is already a 0.011 hue step - so
        # below this gate the "hue" being compared is quantisation, not intent.
        if colors.saturation_of(out) > 0.15:
            assert colors.hue_distance(rgb, out) < 0.01, f"hue moved at {step:+d}"


def test_value_is_never_touched():
    """This is purely a saturation control at every setting - brightness has
    its own slider, and two controls doing the same thing would fight."""
    rgb = colors.parse_color("be3f50")
    for step in range(-10, 11):
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
