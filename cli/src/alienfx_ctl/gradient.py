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


def render_kbd(keymap, first, second, axis: str = DEFAULT_AXIS):
    """Render the gradient across the keyboard.

    Returns a list of ``(led_index, r, g, b)`` sorted by index, ready to hand
    to ``apiv5.paint``.  Keys the map has no grid position for land mid-blend
    rather than being dropped, so an incomplete keymap still lights fully.
    """
    key_to_index = keymap.get("key_to_index") or {}
    grid_positions = keymap.get("grid_positions") or {}
    if not key_to_index:
        raise GradientError("keymap has no key_to_index")

    max_row, max_col = grid_extent(grid_positions) if grid_positions else (0, 0)

    leds = []
    for key, index in key_to_index.items():
        position = grid_positions.get(key)
        if position is None:
            ratio = 0.5
        else:
            row, col = _row_col(position)
            ratio = sample_axis(row, col, max_row, max_col, axis)
        red, green, blue = lerp_rgb(first, second, ratio)
        leds.append((int(index), red, green, blue))
    leds.sort(key=lambda led: led[0])
    return leds


def elc_samples(first, second) -> dict:
    """Return ``{zone: rgb}`` for the three chassis zones."""
    return {
        zone: lerp_rgb(first, second, ratio)
        for zone, ratio in ELC_ANCHORS.items()
    }
