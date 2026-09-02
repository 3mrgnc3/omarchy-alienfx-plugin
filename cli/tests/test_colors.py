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
