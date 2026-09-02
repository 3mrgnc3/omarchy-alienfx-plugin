"""The blended diagonal gradient."""

import pytest

from alienfx_ctl import gradient


def test_lerp_endpoints_are_exact():
    """t=0 and t=1 must return the chosen colours untouched - the whole reason
    interpolation happens in sRGB rather than linear space."""
    a, b = (255, 120, 0), (0, 60, 255)
    assert gradient.lerp_rgb(a, b, 0.0) == a
    assert gradient.lerp_rgb(a, b, 1.0) == b


def test_lerp_midpoint():
    assert gradient.lerp_rgb((0, 0, 0), (255, 255, 255), 0.5) == (128, 128, 128)


def test_lerp_clamps_out_of_range_t():
    a, b = (10, 20, 30), (200, 210, 220)
    assert gradient.lerp_rgb(a, b, -5) == a
    assert gradient.lerp_rgb(a, b, 5) == b


def test_diagonal_axis_corners():
    """tl-br: top-left is the near end, bottom-right the far end, and the two
    off-corners both land mid-blend. That is what makes it read as a diagonal."""
    assert gradient.sample_axis(0, 0, 5, 16, "tl-br") == 0.0
    assert gradient.sample_axis(5, 16, 5, 16, "tl-br") == 1.0
    assert gradient.sample_axis(0, 16, 5, 16, "tl-br") == 0.5
    assert gradient.sample_axis(5, 0, 5, 16, "tl-br") == 0.5


def test_anti_diagonal_axis_corners():
    assert gradient.sample_axis(0, 16, 5, 16, "tr-bl") == 0.0
    assert gradient.sample_axis(5, 0, 5, 16, "tr-bl") == 1.0


def test_linear_axes():
    assert gradient.sample_axis(3, 0, 5, 16, "lr") == 0.0
    assert gradient.sample_axis(3, 16, 5, 16, "lr") == 1.0
    assert gradient.sample_axis(0, 9, 5, 16, "tb") == 0.0
    assert gradient.sample_axis(5, 9, 5, 16, "tb") == 1.0


def test_single_row_or_column_does_not_divide_by_zero():
    assert gradient.sample_axis(0, 0, 0, 0, "tl-br") == 0.0


def test_unknown_axis_is_rejected():
    with pytest.raises(gradient.GradientError):
        gradient.sample_axis(0, 0, 5, 16, "sideways")


def _keymap():
    return {
        "key_to_index": {"a": 0, "b": 1, "c": 2, "d": 3},
        "grid_positions": {
            "a": {"row": 0, "col": 0},
            "b": {"row": 0, "col": 1},
            "c": {"row": 1, "col": 0},
            "d": {"row": 1, "col": 1},
        },
    }


def test_render_kbd_spans_the_anchors_and_sorts_by_index():
    leds = gradient.render_kbd(_keymap(), (0, 0, 0), (255, 255, 255))
    # Sorted, contiguous, and covering the whole range the controller accepts -
    # not just the mapped keys. See the coverage tests below for why.
    assert [led[0] for led in leds[:4]] == [0, 1, 2, 3]
    assert leds[0][1:] == (0, 0, 0)        # top-left key
    assert leds[3][1:] == (255, 255, 255)  # bottom-right key


def test_render_kbd_accepts_list_grid_positions():
    km = _keymap()
    km["grid_positions"] = {k: [v["row"], v["col"]] for k, v in km["grid_positions"].items()}
    leds = gradient.render_kbd(km, (0, 0, 0), (255, 255, 255))
    assert leds[0][1:] == (0, 0, 0)
    assert leds[3][1:] == (255, 255, 255)


def test_keys_without_a_grid_position_still_light():
    """An incomplete keymap should leave keys mid-blend rather than dark."""
    km = _keymap()
    km["key_to_index"]["orphan"] = 9
    leds = dict((led[0], led[1:]) for led in gradient.render_kbd(km, (0, 0, 0), (255, 255, 255)))
    assert leds[9] == (128, 128, 128)


def test_render_kbd_needs_a_keymap():
    with pytest.raises(gradient.GradientError):
        gradient.render_kbd({}, (0, 0, 0), (1, 1, 1))


def test_elc_samples_anchor_the_chassis():
    samples = gradient.elc_samples((255, 0, 0), (0, 0, 255))
    assert samples["logo"] == (255, 0, 0)    # near end
    assert samples["pbtn"] == (0, 0, 255)    # far end
    assert samples["tpd"] == (128, 0, 128)   # mid-blend
    assert set(samples) == set(gradient.ELC_ANCHORS)


def test_grid_extent():
    assert gradient.grid_extent(_keymap()["grid_positions"]) == (1, 1)


# NOTE: an earlier attempt at the stuck-key problem made render_kbd cover every
# index in the strip, filling unnamed ones from their nearest neighbour. It is
# reverted: writing distinct colours to indices this keyboard does not have
# produced uneven, multi-coloured output. apiv5.solid gets away with the full
# range only because every LED carries the same colour. The tests that pinned
# that behaviour are gone with it.


# ------------------------------------------------- other machines, other zones

def test_elc_samples_uses_the_reference_anchors_by_default():
    samples = gradient.elc_samples((255, 0, 0), (0, 0, 255))
    assert samples["logo"] == (255, 0, 0)
    assert samples["pbtn"] == (0, 0, 255)
    assert samples["tpd"] == (128, 0, 128)


def test_elc_samples_honours_a_named_subset():
    """A model with fewer zones must not be handed samples for zones it lacks."""
    samples = gradient.elc_samples((255, 0, 0), (0, 0, 255), ["logo", "tpd"])
    assert set(samples) == {"logo", "tpd"}


def test_elc_samples_spreads_unknown_zone_names_evenly():
    """An unfamiliar zone set has nothing model-specific to anchor to, so it
    gets spread across the axis rather than defaulting to one end."""
    samples = gradient.elc_samples((0, 0, 0), (255, 255, 255), ["a", "b", "c"])
    assert samples["a"] == (0, 0, 0)
    assert samples["b"] == (128, 128, 128)
    assert samples["c"] == (255, 255, 255)


def test_elc_samples_with_a_single_zone_lands_mid_blend():
    samples = gradient.elc_samples((0, 0, 0), (255, 255, 255), ["only"])
    assert samples["only"] == (128, 128, 128)
