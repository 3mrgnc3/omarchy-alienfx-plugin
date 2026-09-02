"""The blended diagonal gradient.

Interpolation happens in sRGB byte space rather than linear-RGB.  That is not
an oversight: at t=0 and t=1 the user must get exactly the two colours they
chose, and the midpoint should look like the midpoint they expect on a
keyboard, not the photometrically-correct blend.

Only the keyboard is addressable per key, so it carries the real gradient.  The
three chassis zones are single-colour each and are sampled at fixed points on
the same axis, which is what makes the whole chassis read as one blend.
"""

from __future__ import annotations

import bisect

from .apiv5 import KBD_LED_COUNT

AXES = ("tl-br", "tr-bl", "lr", "tb")
DEFAULT_AXIS = "tl-br"

#: Where each chassis zone sits on the gradient axis.  The lid logo anchors the
#: near end, the touchpad sits mid-blend, the power button anchors the far end.
ELC_ANCHORS = {"logo": 0.0, "tpd": 0.5, "pbtn": 1.0}


class GradientError(ValueError):
    """Raised for an unknown gradient axis."""


def lerp_rgb(first, second, position: float) -> tuple:
    """Interpolate between two colours. ``position`` is clamped to 0..1."""
    ratio = 0.0 if position < 0.0 else (1.0 if position > 1.0 else float(position))
    return tuple(
        round(start + (end - start) * ratio)
        for start, end in zip(first, second)
    )


def sample_axis(row: int, col: int, max_row: int, max_col: int, axis: str = DEFAULT_AXIS) -> float:
    """Map a grid position to its position ``t`` along the gradient axis."""
    if axis not in AXES:
        raise GradientError(f"unknown axis {axis!r} (expected one of {', '.join(AXES)})")

    row_t = (row / max_row) if max_row else 0.0
    col_t = (col / max_col) if max_col else 0.0

    if axis == "lr":
        return col_t
    if axis == "tb":
        return row_t
    if axis == "tr-bl":
        return (row_t + (1.0 - col_t)) / 2.0
    return (row_t + col_t) / 2.0  # tl-br, the blended diagonal


def grid_extent(grid_positions) -> tuple:
    """Return ``(max_row, max_col)`` across a keymap's grid positions."""
    max_row = max_col = 0
    for position in grid_positions.values():
        row, col = _row_col(position)
        max_row = max(max_row, row)
        max_col = max(max_col, col)
    return max_row, max_col


def _row_col(position) -> tuple:
    """Accept either ``{"row": r, "col": c}`` or a two-item sequence."""
    if isinstance(position, dict):
        return int(position.get("row", 0)), int(position.get("col", 0))
    return int(position[0]), int(position[1])


def _nearest(sorted_indices, index: int) -> int:
    """The mapped index closest to ``index``, preferring the lower on a tie.

    Used to colour LEDs the keymap does not name. See ``render_kbd`` for why
    index proximity is the right measure on this hardware.
    """
    position = bisect.bisect_left(sorted_indices, index)
    if position == 0:
        return sorted_indices[0]
    if position == len(sorted_indices):
        return sorted_indices[-1]
    lower, upper = sorted_indices[position - 1], sorted_indices[position]
    return lower if (index - lower) <= (upper - index) else upper


def render_kbd(keymap, first, second, axis: str = DEFAULT_AXIS,
               led_count: int = KBD_LED_COUNT):
    """Render the gradient across the keyboard.

    Returns ``(led_index, r, g, b)`` for **every** index the controller accepts,
    sorted by index, ready to hand to ``apiv5.paint``.

    Covering the whole range is not optional. A painted frame only writes the
    LEDs it is given and never clears the rest, so an index this function
    skipped would keep whatever the last full-range paint left on it - for as
    long as the user stayed in gradient mode. ``apiv5.solid`` has always written
    all of them, and the two paths disagreeing is exactly how single keys ended
    up stuck on a colour from a previous setting. ``led_count`` comes from
    ``apiv5`` so they cannot drift apart again.

    Unmapped indices take the colour of the nearest mapped index. On this
    hardware that is the physically right answer rather than an approximation:
    the firmware lays LEDs out in contiguous per-row blocks, and a wide key
    (tab, backspace, capslock, enter, either shift, space, backslash) sits over
    two or more adjacent indices while the keymap records only one of them. The
    fill is what makes a wide key's other LED follow the key it belongs to.

    A key with an index but no grid position still lands mid-blend, so an
    incomplete keymap lights fully rather than leaving holes.

    Where an unmapped index sits equidistant between two mapped ones the lower
    wins, which can hand it the neighbouring key's colour instead of its own
    key's. Measured on the reference keymap that costs at most 24/255 per
    channel at full scale - about 7/255 at the brightness these machines
    actually run at - because adjacent indices are adjacent on the gradient
    axis too. Not worth resolving with a cleverer rule: the keymap carries no
    key-width data to resolve it *correctly*, and a guess that is usually right
    is worse than a bounded error that always is.
    """
    key_to_index = keymap.get("key_to_index") or {}
    grid_positions = keymap.get("grid_positions") or {}
    if not key_to_index:
        raise GradientError("keymap has no key_to_index")

    max_row, max_col = grid_extent(grid_positions) if grid_positions else (0, 0)

    mapped = {}
    for key, index in key_to_index.items():
        position = grid_positions.get(key)
        if position is None:
            ratio = 0.5
        else:
            row, col = _row_col(position)
            ratio = sample_axis(row, col, max_row, max_col, axis)
        mapped[int(index)] = lerp_rgb(first, second, ratio)

    if not mapped:
        raise GradientError("keymap produced no usable LED indices")

    known = sorted(mapped)
    leds = []
    for index in range(int(led_count)):
        colour = mapped.get(index)
        if colour is None:
            colour = mapped[_nearest(known, index)]
        leds.append((index, colour[0], colour[1], colour[2]))
    return leds


def elc_samples(first, second) -> dict:
    """Return ``{zone: rgb}`` for the three chassis zones."""
    return {
        zone: lerp_rgb(first, second, ratio)
        for zone, ratio in ELC_ANCHORS.items()
    }
