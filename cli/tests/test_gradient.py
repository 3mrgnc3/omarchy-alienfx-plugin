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


# ------------------------------------------------- full-range LED coverage
#
# A painted frame writes only the LEDs it is given and never clears the rest,
# while apiv5.solid has always written all of them. When render_kbd covered
# only the mapped keys, any unmapped index kept whatever the last solid paint
# left on it - which is how single keys ended up stuck on an old colour for as
# long as the user stayed in gradient mode.

from alienfx_ctl.apiv5 import KBD_LED_COUNT


def _sparse_keymap():
    """Two mapped keys with a deliberate gap, mimicking a wide key's sibling."""
    return {
        "key_to_index": {"a": 10, "b": 12},
        "grid_positions": {"a": {"row": 0, "col": 0}, "b": {"row": 1, "col": 1}},
    }


def test_render_covers_every_index_the_controller_accepts():
    """The two paint paths must agree on how many LEDs exist, or the one that
    writes fewer leaves stale colour behind."""
    leds = gradient.render_kbd(_sparse_keymap(), (255, 0, 0), (0, 0, 255))
    assert [led[0] for led in leds] == list(range(KBD_LED_COUNT))


def test_no_index_is_left_unwritten(shipped_keymap):
    leds = gradient.render_kbd(shipped_keymap, (255, 0, 0), (0, 0, 255))
    written = {led[0] for led in leds}
    assert written == set(range(KBD_LED_COUNT))


def test_the_reported_stuck_key_is_now_painted(shipped_keymap):
    """The bug as reported: the backslash key is mapped to 54, but index 55 -
    its other LED, and the index an older revision of this keymap used for it -
    was never written by a gradient."""
    leds = {i: (r, g, b) for i, r, g, b in
            gradient.render_kbd(shipped_keymap, (255, 0, 0), (0, 0, 255))}
    assert 55 in leds
    # 55's only distance-1 mapped neighbour is 54, so it must match exactly.
    assert leds[55] == leds[54]


@pytest.mark.parametrize("gap,owner", [(41, 40), (55, 54), (60, 61), (82, 81), (106, 107)])
def test_wide_key_siblings_follow_their_key(shipped_keymap, gap, owner):
    """A wide key sits over more than one LED while the keymap records one.
    The unmapped sibling has to track the key it physically belongs to."""
    leds = {i: (r, g, b) for i, r, g, b in
            gradient.render_kbd(shipped_keymap, (255, 0, 0), (0, 0, 255))}
    assert leds[gap] == leds[owner]


def test_a_filled_index_always_copies_some_mapped_index(shipped_keymap):
    """Fills are copies of a real key's colour, never invented values."""
    leds = gradient.render_kbd(shipped_keymap, (255, 0, 0), (0, 0, 255))
    by_index = {i: (r, g, b) for i, r, g, b in leds}
    mapped = set(shipped_keymap["key_to_index"].values())
    palette = {by_index[i] for i in mapped}
    for index, red, green, blue in leds:
        assert (red, green, blue) in palette


def test_fill_error_against_an_adjacent_key_stays_bounded(shipped_keymap):
    """Ties resolve to the lower index, which can hand an LED its neighbour's
    colour rather than its own key's. That is acceptable only while the error
    stays small; pin the bound so a future change cannot widen it silently."""
    by_index = {i: (r, g, b) for i, r, g, b in
                gradient.render_kbd(shipped_keymap, (0, 0, 0), (255, 255, 255))}
    mapped = set(shipped_keymap["key_to_index"].values())
    worst = 0
    for index in range(KBD_LED_COUNT):
        if index in mapped:
            continue
        for neighbour in (index - 1, index + 1):
            if neighbour in mapped:
                worst = max(worst, max(abs(a - b) for a, b in
                                       zip(by_index[index], by_index[neighbour])))
    assert worst <= 48, f"a filled LED is {worst}/255 from an adjacent key"


def test_endpoints_survive_the_full_range_fill(shipped_keymap):
    """Covering unmapped indices must not disturb the anchors."""
    by_index = {i: (r, g, b) for i, r, g, b in
                gradient.render_kbd(shipped_keymap, (255, 0, 0), (0, 0, 255))}
    grid = shipped_keymap["grid_positions"]
    k2i = shipped_keymap["key_to_index"]
    top_left = min(k2i, key=lambda k: (grid[k]["row"] + grid[k]["col"]))
    assert by_index[k2i[top_left]] == (255, 0, 0)


def test_led_count_comes_from_the_protocol_module():
    """Redefining it here is how the two paths drifted apart in the first place."""
    from alienfx_ctl import apiv5
    assert gradient.KBD_LED_COUNT is apiv5.KBD_LED_COUNT


def test_a_custom_led_count_is_honoured():
    leds = gradient.render_kbd(_sparse_keymap(), (1, 1, 1), (2, 2, 2), led_count=20)
    assert [led[0] for led in leds] == list(range(20))


@pytest.mark.parametrize("index,expected", [
    (0, 10), (9, 10), (10, 10), (11, 10), (12, 12), (13, 12), (199, 12),
])
def test_nearest_picks_the_closest_mapped_index(index, expected):
    assert gradient._nearest([10, 12], index) == expected
