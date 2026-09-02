"""Config root, live state, and named profiles.

Everything this tool owns lives under a single directory so an uninstall knows
exactly what to keep and the plugin knows exactly what to watch:

    ~/.config/omarchy-alienfx-plugin/
        current.json         live state - what the lights are showing now
        current-profile      name of the profile that is loaded, if any
        themesync.enabled    flag file; presence means ThemeSync is on
        profiles/<name>.json named profiles
        keymap/keymap.json   the active per-key map

Live state is written on every change, so a restore after reboot replays
exactly what the user last chose.  Writes are atomic: a power cut mid-write
leaves the previous state intact rather than a truncated file.
"""

from __future__ import annotations

import json
import os
import re
import tempfile

APP_DIR = "omarchy-alienfx-plugin"

#: Profile names become filenames, so they are restricted to a safe stem.
#: This blocks path traversal, dotfiles, and names that upset cloud sync.
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")

DEFAULT_PROFILE = "Default"

#: 26/255 is about 10% - ambient light, not a stage light. Matches how these
#: machines are actually used at a desk.
DEFAULT_BRIGHTNESS = 26

DEFAULT_STATE = {
    "themesync": True,
    "zonesync": True,
    "effect": "gradient",
    "axis": "tl-br",
    "brightness": DEFAULT_BRIGHTNESS,
    "speed": "medium",
    # Keycaps sit behind a diffuser that washes colour out; a saturation push
    # makes a theme colour read as the colour the user actually picked.
    "saturation": 2.4,
    "selected_zone": "kbd",
    "zones": {
        "kbd": {"color": "ff7800"},
        "tpd": {"color": "ff7800"},
        "logo": {"color": "ff7800"},
        "pbtn": {"color": "ff7800"},
    },
}


class StateError(RuntimeError):
    """Raised for invalid profile names or unreadable state."""


def config_root() -> str:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, APP_DIR)


def state_path() -> str:
    return os.path.join(config_root(), "current.json")


def profiles_dir() -> str:
    return os.path.join(config_root(), "profiles")


def keymap_dir() -> str:
    return os.path.join(config_root(), "keymap")


def themesync_flag() -> str:
    return os.path.join(config_root(), "themesync.enabled")


def current_profile_marker() -> str:
    return os.path.join(config_root(), "current-profile")


def ensure_dirs() -> None:
    for path in (config_root(), profiles_dir(), keymap_dir()):
        os.makedirs(path, exist_ok=True)


def write_json_atomic(path: str, data) -> None:
    """Write JSON via a temp file in the same directory, then rename."""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=directory, prefix=".tmp-", delete=False
    )
    try:
        with handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(handle.name, path)
    except Exception:
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        raise


def read_json(path: str, default=None):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return default


def _merge_defaults(loaded) -> dict:
    """Fill any missing key from the defaults.

    Keeps an older or hand-edited state file working after an upgrade adds a
    field, instead of raising on the first missing key.
    """
    merged = json.loads(json.dumps(DEFAULT_STATE))
    if isinstance(loaded, dict):
        for key, value in loaded.items():
            if key == "zones" and isinstance(value, dict):
                for zone, zone_value in value.items():
                    if isinstance(zone_value, dict):
                        merged["zones"].setdefault(zone, {}).update(zone_value)
            else:
                merged[key] = value
    # The flag file is the single source of truth for ThemeSync, so the shell
    # hook and the CLI can never disagree about whether syncing is on.
    merged["themesync"] = os.path.exists(themesync_flag())
    return merged


def load_state() -> dict:
    """Load live state, falling back to defaults for anything absent."""
    return _merge_defaults(read_json(state_path()))


def save_state(new_state) -> None:
    payload = {k: v for k, v in new_state.items() if k != "themesync"}
    ensure_dirs()
    write_json_atomic(state_path(), payload)


def themesync_enabled() -> bool:
    return os.path.exists(themesync_flag())


def set_themesync(enabled: bool) -> None:
    ensure_dirs()
    path = themesync_flag()
    if enabled:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("ThemeSync is on. Delete this file (or run 'alienfx-ctl themesync off') to disable.\n")
    else:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass


def validate_name(name: str) -> str:
    """Validate a profile name for use as a filename stem."""
    text = str(name or "").strip()
    if not text:
        raise StateError("profile name cannot be empty")
    if not _NAME_RE.match(text):
        raise StateError(
            f"invalid profile name {text!r}: use letters, digits, '-' and '_', "
            "starting with a letter or digit"
        )
    return text


def profile_path(name: str) -> str:
    safe = validate_name(name)
    path = os.path.join(profiles_dir(), f"{safe}.json")
    # Belt and braces: the name is already constrained, but confirm the result
    # really is inside the profiles directory before any I/O touches it.
    root = os.path.realpath(profiles_dir())
    resolved = os.path.realpath(os.path.dirname(path))
    if resolved != root:
        raise StateError(f"refusing to use a profile path outside {root}")
    return path


def list_profiles():
    try:
        names = os.listdir(profiles_dir())
    except OSError:
        return []
    found = []
    for entry in names:
        if not entry.endswith(".json"):
            continue
        stem = entry[: -len(".json")]
        if _NAME_RE.match(stem):
            found.append(stem)
    return sorted(found, key=str.lower)


def save_profile(name: str, payload) -> str:
    path = profile_path(name)
    ensure_dirs()
    data = {k: v for k, v in payload.items() if k != "themesync"}
    write_json_atomic(path, data)
    return path


def load_profile(name: str) -> dict:
    path = profile_path(name)
    data = read_json(path)
    if data is None:
        raise StateError(f"profile {name!r} not found or unreadable")
    return _merge_defaults(data)


def rename_profile(old: str, new: str) -> str:
    source = profile_path(old)
    target = profile_path(new)
    if not os.path.isfile(source):
        raise StateError(f"profile {old!r} not found")
    os.replace(source, target)
    if current_profile() == validate_name(old):
        set_current_profile(validate_name(new))
    return target


def delete_profile(name: str) -> None:
    try:
        os.unlink(profile_path(name))
    except FileNotFoundError:
        raise StateError(f"profile {name!r} not found") from None


def current_profile():
    """Name of the loaded profile, or None.

    The marker is re-validated on read: a tampered or corrupt marker reports
    'no profile' rather than being used to build a path.
    """
    try:
        with open(current_profile_marker(), "r", encoding="utf-8") as fh:
            text = fh.read().strip()
    except OSError:
        return None
    if not text or not _NAME_RE.match(text):
        return None
    return text


def set_current_profile(name) -> None:
    ensure_dirs()
    if name is None:
        try:
            os.unlink(current_profile_marker())
        except FileNotFoundError:
            pass
        return
    safe = validate_name(name)
    directory = os.path.dirname(current_profile_marker())
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=directory, prefix=".tmp-", delete=False
    )
    with handle:
        handle.write(safe + "\n")
    os.replace(handle.name, current_profile_marker())
