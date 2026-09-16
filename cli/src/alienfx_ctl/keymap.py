"""Per-key keymap loading, validation and import.

A keymap ties a physical key name to its LED index and its position on the key
grid.  The gradient needs the grid positions; without them there is nothing to
interpolate across.  A map for this model ships with the package, but keyboard
layouts differ by region and by machine, so a user map always wins.
"""

from __future__ import annotations

import json
import os
import re

from . import hardware, state
from .apiv5 import KBD_LED_COUNT

SHIPPED_KEYMAP = os.path.join(os.path.dirname(__file__), "data", "m16r2-keymap.json")

_REQUIRED_KEYS = ("key_to_index", "grid_positions")

# ---------------------------------------------------------------- limits
#
# A keymap is UNTRUSTED INPUT. The whole point of the contribution flow is that
# people send each other these files, and one may be hostile or simply corrupt.
# Nothing here repairs a bad value; it is rejected, because a keymap that paints
# the wrong keys is more confusing than a clear error.
#
# The bounds are generous against real data - the reference keymap has 85 keys,
# the longest name is 10 characters of lowercase letters and digits, and the
# grid reaches row 5, column 16 - so a legitimate keyboard, including a much
# larger one, fits comfortably.

#: Refuse to read a file larger than this before parsing it. The reference
#: keymap is 8.8KB. Parsing is what allocates, so the check has to come first.
MAX_FILE_BYTES = 1 << 20

#: Enough for any keyboard with a keypad and a macro column, and small enough
#: that a hostile file cannot drive a huge paint loop.
MAX_KEYS = 512

#: Key and zone names. Long enough for "apostrophe" several times over.
MAX_NAME_LENGTH = 32

#: Grid bounds. `layout.render` indents by column, so an unbounded column is a
#: memory-exhaustion primitive: col=10**9 asked for a six-gigabyte line.
MAX_GRID_ROW = 31
MAX_GRID_COL = 63

#: Free text shown to the user: the model name, the Fn legends.
MAX_TEXT_LENGTH = 64

#: Chassis lights. An id becomes one byte of a HID packet; more ids than this
#: could not fit the report anyway.
MAX_ZONE_ID = 255
MAX_ZONES = 32
MAX_IDS_PER_ZONE = 16

#: Names are restricted rather than merely length-capped. They are used as
#: dictionary keys, matched against layouts, and - the reason that matters -
#: printed into the wizard's terminal while it is in raw mode. A name carrying
#: escape bytes can retitle the window, clear the screen, or hide text in the
#: very display the user is reading to map their keys.
_SAFE_NAME = re.compile(r"^[a-z0-9_]{1,%d}$" % MAX_NAME_LENGTH)

#: Free text is allowed punctuation, because legends look like `` ` ~ `` and
#: "F7 (kbd_backlight)". Control characters are not, for the same reason.
_SAFE_TEXT = re.compile(r"^[ -~]{0,%d}$" % MAX_TEXT_LENGTH)


def _clip(value) -> str:
    """A value safe to put in an error message.

    repr() escapes control characters, so the message cannot itself carry an
    escape sequence to the terminal; the truncation stops a megabyte-long name
    being echoed back.
    """
    text = repr(value)
    return text if len(text) <= 40 else text[:37] + "..."


def _check_name(value, where: str) -> str:
    if not isinstance(value, str):
        raise KeymapError(f"{where}: name must be a string, got {_clip(value)}")
    if not _SAFE_NAME.match(value):
        raise KeymapError(
            f"{where}: name {_clip(value)} is not allowed - names must be 1 to "
            f"{MAX_NAME_LENGTH} characters of a-z, 0-9 or underscore")
    return value


def _check_text(value, where: str) -> str:
    if not isinstance(value, str):
        raise KeymapError(f"{where}: must be text, got {_clip(value)}")
    if not _SAFE_TEXT.match(value):
        raise KeymapError(
            f"{where}: {_clip(value)} is not allowed - printable ASCII only, "
            f"at most {MAX_TEXT_LENGTH} characters")
    return value


def _check_int(value, where: str, low: int, high: int) -> int:
    # bool is an int in Python, and True would sail through a range check.
    if not isinstance(value, int) or isinstance(value, bool):
        raise KeymapError(f"{where}: must be an integer, got {_clip(value)}")
    if not low <= value <= high:
        raise KeymapError(f"{where}: {value} is outside {low}..{high}")
    return value

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
    """Whether the user has probed a keymap of their own on this machine."""
    return os.path.isfile(model_keymap_path()) or os.path.isfile(legacy_keymap_path())


def has_keymap_for_this_machine() -> bool:
    """Whether a keymap that actually describes *this* model is available.

    Distinct from ``has_user_keymap``, and the difference is what the popup's
    first-run wizard prompt should turn on. "The user has not probed one
    themselves" is not the same as "this machine has no map": an owner of the
    reference model, or of any model someone has contributed a keymap for, is
    already mapped correctly and has nothing to do.

    Three ways to be mapped, in the order ``load`` prefers them:

    1. the user's own keymap,
    2. one contributed for this exact model and bundled with the plugin,
    3. the reference keymap, but only when this machine *is* that model. On
       anything else it is the wrong map, the keys light in the wrong places,
       and the wizard is exactly what the user needs.
    """
    if has_user_keymap() or os.path.isfile(bundled_keymap_path()):
        return True
    try:
        reference = load_file(SHIPPED_KEYMAP)
    except (KeymapError, OSError):
        return False
    described = str(reference.get("device") or "")
    return bool(described) and hardware.slug(described) == hardware.slug()


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

    **Treats the keymap as untrusted input.** These files are meant to be shared
    between users, so one may be hostile or simply corrupt. Every value is
    checked for type, length and range; nothing is repaired, because a keymap
    that paints the wrong keys is more confusing than a clear error.

    The checks that are about safety rather than correctness:

    - Names are restricted to ``a-z0-9_`` and free text to printable ASCII,
      because both are printed into the wizard's terminal while it is in raw
      mode. A name carrying escape bytes could retitle the window, clear the
      screen, or hide text in the display the user is reading to map their keys.
    - Grid columns are bounded, because ``layout.render`` indents by column - an
      unbounded one is a memory-exhaustion primitive.
    - The key count is bounded, so a file cannot drive an enormous paint loop.
    - Chassis light ids are bounded to a byte, because each becomes one byte of
      a HID packet; an out-of-range id used to reach ``bytes()`` and raise.
    """
    if not isinstance(data, dict):
        raise KeymapError("keymap must be a JSON object")

    kind = data.get("keyboard") or KEYBOARD_PER_KEY
    if kind not in (KEYBOARD_PER_KEY, KEYBOARD_ZONES):
        raise KeymapError(
            f"unknown keyboard kind {_clip(kind)} (expected "
            f"{KEYBOARD_PER_KEY!r} or {KEYBOARD_ZONES!r})")

    # Free text that reaches the user's terminal by way of `keymap show`.
    for field in ("device", "model_slug", "vid_pid"):
        if field in data and data[field] is not None:
            _check_text(data[field], f"{field}")

    _check_zones(data)

    if kind == KEYBOARD_ZONES:
        # No per-key data to check - the keyboard is chassis zones - but there
        # had better be some zones, or the keymap describes nothing at all.
        if not data.get("zones"):
            raise KeymapError(
                f"a '{KEYBOARD_ZONES}' keymap needs a non-empty 'zones' object")
        return data

    for key in _REQUIRED_KEYS:
        if not isinstance(data.get(key), dict) or not data[key]:
            raise KeymapError(f"keymap is missing a non-empty '{key}' object")

    key_to_index = data["key_to_index"]
    grid_positions = data["grid_positions"]

    if len(key_to_index) > MAX_KEYS:
        raise KeymapError(
            f"keymap names {len(key_to_index)} keys; the limit is {MAX_KEYS}")
    if len(grid_positions) > MAX_KEYS:
        raise KeymapError(
            f"keymap positions {len(grid_positions)} keys; the limit is {MAX_KEYS}")

    for name, index in key_to_index.items():
        _check_name(name, "key_to_index")
        _check_int(index, f"key_to_index[{_clip(name)}]", 0, _MAX_LED_INDEX)

    for name, position in grid_positions.items():
        _check_name(name, "grid_positions")
        where = f"grid_positions[{_clip(name)}]"
        if isinstance(position, dict):
            if "row" not in position or "col" not in position:
                raise KeymapError(f"{where} needs 'row' and 'col'")
            row, col = position["row"], position["col"]
        elif isinstance(position, (list, tuple)) and len(position) == 2:
            row, col = position
        else:
            raise KeymapError(f"{where} must be {{row, col}} or [row, col]")
        _check_int(row, f"{where}.row", 0, MAX_GRID_ROW)
        _check_int(col, f"{where}.col", 0, MAX_GRID_COL)

    legends = data.get("secondary_functions")
    if legends is not None:
        if not isinstance(legends, dict):
            raise KeymapError("secondary_functions must be an object")
        if len(legends) > MAX_KEYS:
            raise KeymapError(f"secondary_functions has more than {MAX_KEYS} entries")
        for name, value in legends.items():
            _check_name(name, "secondary_functions")
            _check_text(value, f"secondary_functions[{_clip(name)}]")

    return data


def _check_zones(data) -> None:
    """Validate a keymap's chassis zone block, if it has one."""
    declared = data.get("zones")
    if declared is None:
        return
    if not isinstance(declared, dict):
        raise KeymapError("zones must be an object")
    if len(declared) > MAX_ZONES:
        raise KeymapError(f"keymap declares {len(declared)} zones; the limit is {MAX_ZONES}")
    for name, ids in declared.items():
        _check_name(name, "zones")
        where = f"zones[{_clip(name)}]"
        if isinstance(ids, int) and not isinstance(ids, bool):
            ids = [ids]
        if not isinstance(ids, (list, tuple)):
            raise KeymapError(f"{where}: must be a light id or a list of them")
        if not ids:
            raise KeymapError(f"{where}: no light ids")
        if len(ids) > MAX_IDS_PER_ZONE:
            raise KeymapError(
                f"{where}: {len(ids)} light ids; the limit is {MAX_IDS_PER_ZONE}")
        for light in ids:
            _check_int(light, where, 0, MAX_ZONE_ID)


def load_file(path: str) -> dict:
    try:
        # Size first: parsing is what allocates, so checking afterwards would be
        # checking after the damage. The reference keymap is 8.8KB.
        size = os.path.getsize(path)
        if size > MAX_FILE_BYTES:
            raise KeymapError(
                f"{path}: {size} bytes is larger than the {MAX_FILE_BYTES} byte limit")
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except RecursionError as exc:
        raise KeymapError(f"{path}: JSON nested too deeply") from exc
    except UnicodeDecodeError as exc:
        raise KeymapError(f"{path}: not valid UTF-8 ({exc})") from exc
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
