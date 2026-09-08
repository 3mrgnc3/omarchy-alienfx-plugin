"""The blended diagonal gradient."""

import pytest

from alienfx_ctl import colors, gradient


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
    from alienfx_ctl import colors as c
    km = _keymap()
    km["key_to_index"]["orphan"] = 9
    leds = dict((led[0], led[1:]) for led in gradient.render_kbd(km, (0, 0, 0), (255, 255, 255)))
    assert leds[9] == c.lerp_perceptual((0, 0, 0), (255, 255, 255), 0.5)
    assert leds[9] != (0, 0, 0), "must not be dark"


def test_render_kbd_needs_a_keymap():
    with pytest.raises(gradient.GradientError):
        gradient.render_kbd({}, (0, 0, 0), (1, 1, 1))


def test_elc_samples_covers_every_reference_zone():
    """With no zone list, every zone the reference machine has gets a colour.
    Their actual values are pinned further down, with the perceptual curve."""
    samples = gradient.elc_samples((255, 0, 0), (0, 0, 255))
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

def test_elc_samples_endpoints_are_the_anchors_exactly():
    """The zones at each end of the axis must be the anchor colours byte for
    byte, not round-tripped approximations of them."""
    samples = gradient.elc_samples((255, 0, 0), (0, 0, 255))
    assert samples["logo"] == (255, 0, 0)
    assert samples["pbtn"] == (0, 0, 255)


def test_elc_samples_interpolate_perceptually_not_in_raw_srgb():
    """The midpoint is taken through Oklab. A raw sRGB midpoint between two
    near-opposite hues dips dark and muddy; Oklab keeps apparent lightness
    even. This is not linear-RGB, which the previous generation rejected for
    being too *light* in the middle."""
    from alienfx_ctl import colors as c
    first, second = (190, 0, 25), (0, 190, 165)
    mid = gradient.elc_samples(first, second)["tpd"]
    assert mid == c.lerp_perceptual(first, second, 0.5)
    assert max(mid) > max(gradient.lerp_rgb(first, second, 0.5)), \
        "should be lighter than the raw sRGB midpoint"


def test_elc_samples_honours_a_named_subset():
    """A model with fewer zones must not be handed samples for zones it lacks."""
    samples = gradient.elc_samples((255, 0, 0), (0, 0, 255), ["logo", "tpd"])
    assert set(samples) == {"logo", "tpd"}


def test_elc_samples_spreads_unknown_zone_names_evenly():
    """An unfamiliar zone set has nothing model-specific to anchor to, so it
    gets spread across the axis rather than defaulting to one end."""
    samples = gradient.elc_samples((0, 0, 0), (255, 255, 255), ["a", "b", "c"])
    assert samples["a"] == (0, 0, 0)
    assert samples["c"] == (255, 255, 255)
    assert samples["a"] != samples["b"] != samples["c"]


def test_elc_samples_with_a_single_zone_lands_mid_blend():
    from alienfx_ctl import colors as c
    samples = gradient.elc_samples((0, 0, 0), (255, 255, 255), ["only"])
    assert samples["only"] == c.lerp_perceptual((0, 0, 0), (255, 255, 255), 0.5)


# --------------------------------------------------- the corner keys

def test_extreme_indices_finds_the_diagonal_corners(shipped_keymap):
    """Chassis zones take a corner key's exact colour rather than a fixed point
    on the axis, because the two differ: `esc` is at t=0.000 but the
    bottom-right key is at t=0.969, since the grid's widest column is the media
    keys at 16 while the arrow is at 13."""
    near, far = gradient.extreme_indices(shipped_keymap)
    k2i = shipped_keymap["key_to_index"]
    assert near == k2i["esc"]
    assert far == k2i["right"]


def test_extreme_indices_follows_the_axis(shipped_keymap):
    """Reversing the diagonal must swap the corners, or a non-default axis
    would silently keep the old machine's corners."""
    a, b = gradient.extreme_indices(shipped_keymap, "tl-br")
    c, d = gradient.extreme_indices(shipped_keymap, "tr-bl")
    assert (a, b) != (c, d)


def test_extreme_indices_needs_a_keymap():
    with pytest.raises(gradient.GradientError):
        gradient.extreme_indices({})


# ------------------------------------------------------ smoothness guards
#
# The three-band regression was invisible to every test above, which is the
# real lesson of it. These run against the shipped keymap and a real pair of
# theme anchors, and walk the diagonal the way the user's eye does.
#
# They deliberately exercise the whole engine path rather than gradient.py
# alone, because the bug was never in the interpolation - it was in `_shape`
# being applied to all 85 interpolated keys instead of the two anchors, which
# clamped their saturation and erased the ramp the blend is made of.

#: A real pair of theme anchors - aetheria's primary and the secondary the
#: palette picks for it - so the walk is over colours the user actually sees.
_NEAR_HEX, _FAR_HEX = "be061f", "2ebaa1"


def _diagonal_walk(shipped_keymap, intensity, brightness=255):
    """The rendered keyboard, ordered the way the gradient runs across it.

    Goes through ``engine.plan`` rather than ``gradient`` alone, because the
    regression was never in the interpolation - it was ``_shape`` being applied
    to all 85 interpolated keys instead of the two anchors. Ordering comes from
    ``gradient.key_ratios``, the same walk the renderer itself uses.
    """
    from alienfx_ctl import engine, state
    st = dict(state.DEFAULT_STATE)
    st.update(brightness=brightness, intensity=intensity, effect="gradient",
              color=_NEAR_HEX, secondary=_FAR_HEX)
    leds = {i: (r, g, b) for i, r, g, b in engine.plan(st, ["kbd"])["kbd_leds"]}
    return [leds[index] for _, index in gradient.key_ratios(shipped_keymap)]


@pytest.mark.parametrize("intensity", [0, 5, 10])
def test_no_hard_seam_anywhere_along_the_diagonal(shipped_keymap, intensity):
    """The real property the three-band regression violated: a *jump*.

    Not "no two adjacent keys are equal" - that was the earlier bar, and it is
    the wrong one now. The blend deliberately holds near each anchor so the two
    chosen colours keep real area, which means neighbouring keys there are
    legitimately identical. What must never happen is a step large enough to
    read as an edge; the regression had a hard hue flip in the middle with flat
    blocks either side. Full scale between two channels is 441, so this bar is
    very tight."""
    walk = _diagonal_walk(shipped_keymap, intensity)
    jumps = [(i, sum((a - b) ** 2 for a, b in zip(walk[i], walk[i - 1])) ** 0.5)
             for i in range(1, len(walk))]
    worst, size = max(jumps, key=lambda pair: pair[1])
    assert size < 60, f"seam of {size:.0f} between keys {worst - 1} and {worst}"


def test_the_two_chosen_colours_keep_real_area(shipped_keymap):
    """Why the blend is eased at all.

    Key density along the diagonal peaks in the middle - 43 of 85 keys sit
    between t=0.4 and t=0.6 - so an even blend spends most of the *surface* on
    the intermediate hues. Measured before easing: 73 of 85 keys were mid-blend
    and each chosen colour showed on about six. It read as a mostly-orange
    keyboard with a hint of the theme in two corners.

    So both anchors must hold a real share of the keys, and the crossover has to
    stay a band rather than becoming the background.

    Distance comes from ``colors.hue_distance``, which wraps the wheel. Rolling
    a subtraction by hand here reported keys 0.6 degrees from the far colour as
    359.4 degrees away, because that anchor happens to land on atan2's branch
    cut.
    """
    walk = _diagonal_walk(shipped_keymap, 0)
    close = 20.0 / 360.0          # hue_distance is in turns, 0..0.5
    near = sum(1 for rgb in walk if colors.hue_distance(rgb, walk[0]) <= close)
    far = sum(1 for rgb in walk if colors.hue_distance(rgb, walk[-1]) <= close)
    assert near >= 15, f"only {near} keys still show the near colour"
    assert far >= 10, f"only {far} keys still show the far colour"
    assert len(walk) - near - far <= 0.6 * len(walk), "the crossover is the background"


@pytest.mark.parametrize("intensity", [0, 5, 10])
def test_the_keyboard_carries_a_broad_spread_of_colours(shipped_keymap, intensity):
    """A floor on distinct colours, so a partial flattening cannot pass by
    changing only every other key."""
    walk = _diagonal_walk(shipped_keymap, intensity)
    assert len(set(walk)) >= 60, f"only {len(set(walk))} of {len(walk)} distinct"


@pytest.mark.parametrize("intensity", [0, 5, 10])
def test_the_blend_travels_one_way_round_the_hue_circle(shipped_keymap, intensity):
    """No doubling back, which would read as a seam.

    Measured on hue rather than on distance in RGB. The blend follows an arc
    now - it holds chroma and rotates hue - and straight-line distance from the
    near anchor is legitimately not monotonic along an arc, so asserting on it
    would fail a correct blend. Hue turning steadily one way is the property
    that actually matters, and it is a stricter check: a blend that stalls or
    reverses shows up immediately."""
    import math
    walk = _diagonal_walk(shipped_keymap, intensity)
    raw = [math.atan2(b, a) for _, a, b in
           (colors._rgb_to_oklab(rgb) for rgb in walk)]

    # Unwrap first. atan2 jumps by a full turn at its branch cut, and this
    # blend crosses it - a raw comparison reported a "-359 degree reversal"
    # that was the cut, not the colour going backwards.
    hues = [raw[0]]
    for angle in raw[1:]:
        step = (angle - hues[-1] + math.pi) % (2.0 * math.pi) - math.pi
        hues.append(hues[-1] + step)

    direction = 1 if hues[-1] >= hues[0] else -1
    reversals = [i for i in range(1, len(hues))
                 if (hues[i] - hues[i - 1]) * direction < -1e-9]
    assert not reversals, f"hue reverses at {reversals[:3]}"
    assert abs(hues[-1] - hues[0]) > math.radians(90), "the blend barely travels"


@pytest.mark.parametrize("intensity", [-10, 0, 10])
def test_the_keyboard_reaches_the_saturation_the_slider_asked_for(
        shipped_keymap, intensity):
    """The user-facing contract: -10 only just tinted, 0 about half saturated,
    +10 about as vivid as sRGB allows.

    Pinned at the three settings that were specified, because those are the ones
    a user reasons about. In between, the response is compressed towards the top
    (see the monotonicity test below) - once the anchors pass roughly 0.6 the
    mid-blend hues are already against the gamut boundary and pushing further
    barely moves them. That is the shape of the sRGB solid, not a curve that can
    be corrected without making it theme-dependent.

    This is the guard a straight a/b blend could never pass: it averaged 47% of
    whatever the anchors were set to, because most keys sit mid-blend where that
    path runs close to neutral, so the keyboard topped out near 0.47 however
    hard the anchors were pushed."""
    import statistics
    walk = _diagonal_walk(shipped_keymap, intensity)
    mean = statistics.mean(colors.saturation_of(rgb) for rgb in walk)
    expected = colors.intensity_saturation(intensity)
    assert mean == pytest.approx(expected, abs=0.08), (
        f"slider asked for {expected:.2f}, keyboard delivered {mean:.2f}")


def test_the_slider_never_moves_backwards(shipped_keymap):
    """Whatever the curve's shape, dragging right must never make the keyboard
    less colourful - that is the one thing that would feel broken."""
    import statistics
    means = [statistics.mean(colors.saturation_of(rgb)
                             for rgb in _diagonal_walk(shipped_keymap, i))
             for i in range(-10, 11)]
    backwards = [i - 10 for i in range(1, len(means)) if means[i] < means[i - 1] - 0.01]
    assert not backwards, f"saturation drops at {backwards}"


def test_the_chassis_corners_match_the_keyboard_corners_exactly(shipped_keymap):
    """In ThemeSync the touchpad ring must be the same colour as `esc` and the
    power button the same as the bottom-right key - byte for byte, not merely
    close. Sampling t=1.0 for the power button is *not* good enough: the right
    arrow sits at t=0.969 because the media keys reach a wider column."""
    from alienfx_ctl import engine, state
    km = shipped_keymap
    st = dict(state.DEFAULT_STATE)
    st.update(brightness=255, intensity=0, effect="gradient",
              color=_NEAR_HEX, secondary=_FAR_HEX)
    plan = engine.plan(st, ["kbd", "tpd", "pbtn", "logo"])
    by_index = {i: (r, g, b) for i, r, g, b in plan["kbd_leds"]}
    esc = by_index[km["key_to_index"]["esc"]]
    corner = by_index[km["key_to_index"]["right"]]
    assert plan["elc"]["tpd"] == esc
    assert plan["elc"]["logo"] == esc
    assert plan["elc"]["pbtn"] == corner
    assert esc != corner, "a real blend, not two ends of nothing"
