"""Per-key keymap loading, validation and import.

A keymap ties a physical key name to its LED index and its position on the key
grid.  The gradient needs the grid positions; without them there is nothing to
interpolate across.  A map for this model ships with the package, but keyboard
layouts differ by region and by machine, so a user map always wins.
"""

from __future__ import annotations

import json
import os

from . import hardware, state
from .apiv5 import KBD_LED_COUNT

SHIPPED_KEYMAP = os.path.join(os.path.dirname(__file__), "data", "m16r2-keymap.json")

_REQUIRED_KEYS = ("key_to_index", "grid_positions")

#: What kind of keyboard a machine has.
#:
#: Most older Alienware laptops light the keyboard as **four APIv4 zones**, not
#: per key - there is no APIv5 controller in them at all. Such a machine is not
#: a special case to be branched around: it is simply a machine with more
#: chassis zones and no keyboard, so its keymap lists ``kb1``..``kb4`` in
#: ``zones`` alongside the logo and power button, and everything downstream
#: already works. ``gradient.elc_samples`` even spreads them along the blend
#: axis, so it gets a theme gradient across the four.
#:
#: Declared rather than probed, so the answer does not change when a controller
#: is briefly missing. Absent means per-key, which is what every keymap written
#: before this existed is.
KEYBOARD_PER_KEY = "per-key"
KEYBOARD_ZONES = "zones"
_MAX_LED_INDEX = 199


class KeymapError(RuntimeError):
    """Raised when a keymap is missing or malformed."""


def model_keymap_path(model: str = "") -> str:
    """Where this machine's keymap belongs, named after its model.

    Per-model rather than a single ``keymap.json`` because zone layouts and LED
    counts differ between Alienware models, and a user may move a config
    directory between machines. The generic name is still honoured on read for
    installs that predate this.
    """
    return os.path.join(state.keymap_dir(), hardware.keymap_filename(model))


def legacy_keymap_path() -> str:
    return os.path.join(state.keymap_dir(), "keymap.json")


def user_keymap_path() -> str:
    """The keymap this machine should use, preferring its model-specific file."""
    model_path = model_keymap_path()
    if os.path.isfile(model_path):
        return model_path
    if os.path.isfile(legacy_keymap_path()):
        return legacy_keymap_path()
    return model_path


def has_user_keymap() -> bool:
    return os.path.isfile(model_keymap_path()) or os.path.isfile(legacy_keymap_path())


def discover_keymaps():
    """Every keymap file we can offer the user, newest-looking first.

    Looks in the keymap directory and beside the package, so the shipped
    reference map and anything the user dropped in are both offered.
    """
    seen, found = set(), []
    directories = [state.keymap_dir(), os.path.dirname(SHIPPED_KEYMAP)]
    for directory in directories:
        try:
            names = sorted(os.listdir(directory))
        except OSError:
            continue
        for name in names:
            if not name.endswith(".json"):
                continue
            path = os.path.join(directory, name)
            real = os.path.realpath(path)
            if real in seen or not os.path.isfile(path):
                continue
            seen.add(real)
            try:
                data = load_file(path)
            except KeymapError:
                continue
            found.append((path, data))
    return found


def validate(data) -> dict:
    """Check a keymap is structurally sound, returning it on success.

    Rejects rather than repairs: a keymap with bad indices would paint the
    wrong keys, which is more confusing than a clear error.
    """
    if not isinstance(data, dict):
        raise KeymapError("keymap must be a JSON object")

    kind = str(data.get("keyboard") or KEYBOARD_PER_KEY)
    if kind not in (KEYBOARD_PER_KEY, KEYBOARD_ZONES):
        raise KeymapError(
            f"unknown keyboard kind {kind!r} (expected "
            f"{KEYBOARD_PER_KEY!r} or {KEYBOARD_ZONES!r})")

    if kind == KEYBOARD_ZONES:
        # No per-key data to check - the keyboard is chassis zones - but there
        # had better be some zones, or the keymap describes nothing at all.
        declared = data.get("zones")
        if not isinstance(declared, dict) or not declared:
            raise KeymapError(
                f"a '{KEYBOARD_ZONES}' keymap needs a non-empty 'zones' object")
        return data

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


def bundled_keymap_path(model: str = "") -> str:
    """Where a keymap contributed for *this* model would live, if one has been.

    Contributed maps are named by the same convention as the user's own -
    ``alienware-<slug>-keymap.json`` - so the lookup is mechanical and adding
    support for a model is a data change with no code behind it.
    """
    return os.path.join(os.path.dirname(SHIPPED_KEYMAP),
                        hardware.keymap_filename(model))


def load() -> dict:
    """The best keymap for this machine.

    In order: the user's own, then one contributed for this exact model, then
    the reference map as a last resort.

    That middle step is the whole point of accepting keymaps from other people.
    Without it a contributed m15 R3 map could sit in the package unused while an
    m15 R3 owner silently got the m16 R2 map - wrong indices, wrong keys, and no
    error to explain it.

    The reference map is still the final fallback rather than an error, because
    a keyboard lit with roughly the right shape beats a dark one, and the wizard
    is one click away.
    """
    if has_user_keymap():
        return load_file(user_keymap_path())
    contributed = bundled_keymap_path()
    if os.path.isfile(contributed):
        return load_file(contributed)
    if os.path.isfile(SHIPPED_KEYMAP):
        return load_file(SHIPPED_KEYMAP)
    raise KeymapError("no keymap available - run 'alienfx-ctl keymap wizard'")


def import_file(source: str, model: str = "") -> str:
    """Validate a keymap file and install it as this machine's keymap."""
    data = load_file(source)
    target = model_keymap_path(model)
    state.ensure_dirs()
    state.write_json_atomic(target, data)
    return target


def keyboard_kind(data=None) -> str:
    """Whether this machine's keyboard is per-key or a set of chassis zones."""
    if data is None:
        try:
            data = load()
        except KeymapError:
            return KEYBOARD_PER_KEY
    return str((data or {}).get("keyboard") or KEYBOARD_PER_KEY)


def has_per_key_keyboard(data=None) -> bool:
    """True unless the keymap says the keyboard is lit as chassis zones."""
    return keyboard_kind(data) != KEYBOARD_ZONES


def zones(data=None) -> dict:
    """The chassis zone map for this machine.

    Zone layouts differ by model - some have fewer zones, some address them at
    different protocol ids - so a keymap may carry its own ``zones`` block. The
    built-in map is the fallback, and is correct for the reference machine.
    """
    from . import device
    if data is None:
        try:
            data = load()
        except KeymapError:
            return dict(device.ELC_ZONES)
    declared = (data or {}).get("zones")
    if not isinstance(declared, dict) or not declared:
        return dict(device.ELC_ZONES)
    out = {}
    for name, ids in declared.items():
        if not isinstance(name, str):
            continue
        if isinstance(ids, int):
            ids = [ids]
        if isinstance(ids, (list, tuple)) and all(isinstance(i, int) for i in ids):
            out[str(name)] = [int(i) for i in ids]
    return out or dict(device.ELC_ZONES)


def led_count(data) -> int:
    """How many LED indices this keyboard actually has.

    Derived from the keymap rather than assumed, because it differs by model.
    ``KBD_LED_COUNT`` is only the ceiling the protocol allows; a given keyboard
    populates some prefix of it. Writing past the real end is wasted packets at
    best, and there is no reason to believe the controller ignores it cleanly.

    Both paint paths must use this same number: when they disagreed, the path
    that wrote fewer left stale colour behind on the difference.
    """
    indices = [int(v) for v in (data.get("key_to_index") or {}).values()]
    if not indices:
        return KBD_LED_COUNT
    return max(1, min(KBD_LED_COUNT, max(indices) + 1))


def describe(data) -> str:
    name = data.get("device", "unknown device")
    total = data.get("total_mapped") or len(data.get("key_to_index", {}))
    zone_names = sorted(zones(data))
    return (f"{name}: {total} keys mapped, {len(zone_names)} chassis zone"
            f"{'' if len(zone_names) == 1 else 's'} ({', '.join(zone_names)})")
