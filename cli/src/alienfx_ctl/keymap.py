"""Per-key keymap loading, validation and import.

A keymap ties a physical key name to its LED index and its position on the key
grid.  The gradient needs the grid positions; without them there is nothing to
interpolate across.  A map for this model ships with the package, but keyboard
layouts differ by region and by machine, so a user map always wins.
"""

from __future__ import annotations

import json
import os

from . import state

SHIPPED_KEYMAP = os.path.join(os.path.dirname(__file__), "data", "m16r2-keymap.json")

_REQUIRED_KEYS = ("key_to_index", "grid_positions")
_MAX_LED_INDEX = 199


class KeymapError(RuntimeError):
    """Raised when a keymap is missing or malformed."""


def user_keymap_path() -> str:
    return os.path.join(state.keymap_dir(), "keymap.json")


def has_user_keymap() -> bool:
    return os.path.isfile(user_keymap_path())


def validate(data) -> dict:
    """Check a keymap is structurally sound, returning it on success.

    Rejects rather than repairs: a keymap with bad indices would paint the
    wrong keys, which is more confusing than a clear error.
    """
    if not isinstance(data, dict):
        raise KeymapError("keymap must be a JSON object")
    for key in _REQUIRED_KEYS:
        if not isinstance(data.get(key), dict) or not data[key]:
            raise KeymapError(f"keymap is missing a non-empty '{key}' object")

    key_to_index = data["key_to_index"]
    grid_positions = data["grid_positions"]

    for name, index in key_to_index.items():
        if not isinstance(index, int) or isinstance(index, bool):
            raise KeymapError(f"key_to_index[{name!r}] must be an integer")
        if not 0 <= index <= _MAX_LED_INDEX:
            raise KeymapError(f"key_to_index[{name!r}] = {index} is outside 0..{_MAX_LED_INDEX}")

    for name, position in grid_positions.items():
        if isinstance(position, dict):
            if "row" not in position or "col" not in position:
                raise KeymapError(f"grid_positions[{name!r}] needs 'row' and 'col'")
            row, col = position["row"], position["col"]
        elif isinstance(position, (list, tuple)) and len(position) == 2:
            row, col = position
        else:
            raise KeymapError(f"grid_positions[{name!r}] must be {{row, col}} or [row, col]")
        for label, value in (("row", row), ("col", col)):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise KeymapError(f"grid_positions[{name!r}].{label} must be a non-negative integer")

    return data


def load_file(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except OSError as exc:
        raise KeymapError(f"{path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise KeymapError(f"{path}: not valid JSON ({exc})") from exc
    return validate(data)


def load() -> dict:
    """Load the user's keymap if present, else the one that ships with us."""
    if has_user_keymap():
        return load_file(user_keymap_path())
    if os.path.isfile(SHIPPED_KEYMAP):
        return load_file(SHIPPED_KEYMAP)
    raise KeymapError("no keymap available - run 'alienfx-ctl keymap wizard'")


def import_file(source: str) -> str:
    """Validate a keymap file and install it as the user's keymap."""
    data = load_file(source)
    target = user_keymap_path()
    state.ensure_dirs()
    state.write_json_atomic(target, data)
    return target


def describe(data) -> str:
    device = data.get("device", "unknown device")
    total = data.get("total_mapped") or len(data.get("key_to_index", {}))
    return f"{device}: {total} keys mapped"
