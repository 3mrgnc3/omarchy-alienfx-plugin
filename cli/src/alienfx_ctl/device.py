"""Device discovery and file-descriptor ownership.

Two separate HID controllers drive the four zones on a supported Alienware
laptop:

* the AW-ELC chassis controller (touchpad halo, lid logo, power button),
  spoken to with APIv4 output reports;
* the per-key keyboard controller, spoken to with APIv5 feature reports.

Nodes are resolved by USB vendor/product id rather than hard-coded to
``/dev/hidraw0``/``hidraw1``.  Node numbering is not stable - it shifts with
what else is plugged in - and writing a lighting packet to whatever happens to
own a given number is how you talk APIv5 at somebody's security key.
"""

from __future__ import annotations

import os
import re

ELC_VID, ELC_PID = 0x187C, 0x0551
KBD_VID, KBD_PID = 0x0D62, 0xD2B1

#: Protocol light ids for the chassis zones of the *reference* machine,
#: confirmed by probing it. Ids 0x01, 0x03 and 0x05-0x09 exist in the address
#: space but drive nothing there.
#:
#: This is a fallback, not the truth. Which chassis zones a machine has, and at
#: which ids, is a property of the model - some have fewer, some have more (Tron
#: strips, a second lid light), some address them differently - so it belongs in
#: the keymap. ``keymap.zones()`` returns the real map and falls back to this.
ELC_ZONES = {"tpd": [0x00], "logo": [0x02], "pbtn": [0x04]}

#: The reference machine's zones. Prefer ``zone_names()``, which asks the
#: keymap; these exist so there is something sane to fall back to and so tests
#: have a fixed set to work against.
ZONES = ("kbd", "tpd", "logo", "pbtn")
ELC_ZONE_NAMES = ("tpd", "logo", "pbtn")

#: The keyboard is the one zone every supported machine has and the only one
#: driven by its own controller. Everything else hangs off the chassis
#: controller, whatever it happens to be called on a given model.
KBD_ZONE = "kbd"

_HID_ID_RE = re.compile(r"^HID_ID=([0-9a-fA-F]+):([0-9a-fA-F]+):([0-9a-fA-F]+)", re.M)
_SYSFS_HIDRAW = "/sys/class/hidraw"


class DeviceError(RuntimeError):
    """Raised when a required device is missing, or is not what it claims."""


def _node_sort_key(name: str) -> tuple:
    match = re.search(r"(\d+)$", name)
    return (int(match.group(1)) if match else 1 << 30, name)


def _read_ids(node: str) -> tuple[int, int] | None:
    """Return the (vendor, product) ids a hidraw node belongs to."""
    path = os.path.join(_SYSFS_HIDRAW, node, "device", "uevent")
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except OSError:
        return None
    match = _HID_ID_RE.search(text)
    if not match:
        return None
    try:
        return int(match.group(2), 16), int(match.group(3), 16)
    except ValueError:
        return None


def iter_nodes():
    """Yield ``(node_name, vid, pid)`` for every hidraw node we can identify."""
    try:
        names = os.listdir(_SYSFS_HIDRAW)
    except OSError:
        return
    for node in sorted(names, key=_node_sort_key):
        ids = _read_ids(node)
        if ids is not None:
            yield node, ids[0], ids[1]


def find_node(vid: int, pid: int) -> str | None:
    """Return the ``/dev/hidrawN`` path for a vendor/product pair, or None.

    Honours ``ALIENFX_ELC_DEV`` / ``ALIENFX_KBD_DEV`` so an unusual machine can
    pin a node by hand; an override still has to pass ``verify_node``.
    """
    override = None
    if (vid, pid) == (ELC_VID, ELC_PID):
        override = os.environ.get("ALIENFX_ELC_DEV")
    elif (vid, pid) == (KBD_VID, KBD_PID):
        override = os.environ.get("ALIENFX_KBD_DEV")
    if override:
        return override

    for node, node_vid, node_pid in iter_nodes():
        if (node_vid, node_pid) == (vid, pid):
            return f"/dev/{node}"
    return None


def verify_node(path: str, vid: int, pid: int) -> None:
    """Re-check a node's identity immediately before writing to it.

    Cheap insurance against a race between discovery and open, and against a
    stale override in the environment.  Anything unexpected is a hard error:
    the alternative is sending vendor lighting packets to an unrelated device.
    """
    node = os.path.basename(os.path.realpath(path))
    ids = _read_ids(node)
    if ids is None:
        raise DeviceError(f"{path}: cannot confirm device identity")
    if ids != (vid, pid):
        raise DeviceError(
            f"{path} is {ids[0]:04x}:{ids[1]:04x}, expected {vid:04x}:{pid:04x} - refusing to write"
        )


def _open_verified(vid: int, pid: int, label: str) -> int:
    path = find_node(vid, pid)
    if path is None:
        raise DeviceError(f"{label} ({vid:04x}:{pid:04x}) not found - is this a supported Alienware?")
    verify_node(path, vid, pid)
    try:
        return os.open(path, os.O_RDWR)
    except PermissionError as exc:
        raise DeviceError(
            f"{path}: permission denied. Install the udev rule "
            "(share/udev/99-omarchy-alienfx.rules) and re-plug or reboot."
        ) from exc
    except OSError as exc:
        raise DeviceError(f"{path}: {exc}") from exc


def elc_zone_names() -> tuple:
    """The chassis zones this machine actually has, from its keymap."""
    from . import keymap
    return tuple(keymap.zones())


def zone_names() -> tuple:
    """Every zone addressable on this machine: the keyboard plus its chassis.

    Asks the keymap rather than assuming the reference machine's four. A model
    with Tron strips or four keyboard zones can then be driven by shipping a
    keymap, with no code change - and because ``gradient.elc_samples`` spreads
    unfamiliar zone names evenly along the blend axis, such a machine gets a
    working theme gradient for free.

    Not cached. A keymap read is 0.17ms measured, which is nothing against the
    63ms an actual chassis packet costs, and this project has twice been bitten
    by caching state that then went stale.
    """
    return (KBD_ZONE,) + elc_zone_names()


def needs_kbd(zones) -> bool:
    return KBD_ZONE in set(zones)


def needs_elc(zones) -> bool:
    """Whether any requested zone lives on the chassis controller.

    Anything that is not the keyboard does, by definition - so this stays
    correct for zone names this build has never heard of.
    """
    return bool(set(zones) - {KBD_ZONE})


# ---------------------------------------------------------------- descriptor cache
#
# One-shot invocations open and close their descriptors, which is right for a
# CLI. Stream mode (see cli.stream) keeps one process alive for the lifetime of
# the popup and applies many changes through it, and there the open cost is pure
# waste per frame - opening the chassis node alone measures ~52ms. Enabling the
# cache makes open_fds hand back the descriptors it already holds.

_cache: dict = {}
_cache_enabled = False


def enable_cache() -> None:
    """Hold descriptors open across calls until close_cache()."""
    global _cache_enabled
    _cache_enabled = True


def close_cache() -> None:
    """Release every held descriptor and go back to open-per-call."""
    global _cache_enabled
    _cache_enabled = False
    for fd in _cache.values():
        try:
            os.close(fd)
        except OSError:
            pass
    _cache.clear()


def cache_enabled() -> bool:
    return _cache_enabled


def open_fds(zones) -> dict:
    """Open exactly the controllers the requested zones need, atomically.

    If any required node fails to open, every descriptor already opened is
    closed before raising, so a partial failure never leaves a leaked fd or a
    half-applied scheme behind.
    """
    wanted = set(zones)
    unknown = wanted - set(zone_names())
    if unknown:
        raise DeviceError(
            f"unknown zone(s): {', '.join(sorted(unknown))}; "
            f"this machine has: {', '.join(zone_names())}")

    if _cache_enabled:
        # Open whatever is missing, then hand back only what was asked for. A
        # failure here leaves the cache holding whatever already worked, which
        # close_cache will release.
        if needs_kbd(wanted) and "kbd" not in _cache:
            _cache["kbd"] = _open_verified(KBD_VID, KBD_PID, "keyboard controller")
        if needs_elc(wanted) and "elc" not in _cache:
            _cache["elc"] = _open_verified(ELC_VID, ELC_PID, "AW-ELC chassis controller")
        held = {}
        if needs_kbd(wanted):
            held["kbd"] = _cache["kbd"]
        if needs_elc(wanted):
            held["elc"] = _cache["elc"]
        return held

    fds: dict = {}
    try:
        if needs_kbd(wanted):
            fds["kbd"] = _open_verified(KBD_VID, KBD_PID, "keyboard controller")
        if needs_elc(wanted):
            fds["elc"] = _open_verified(ELC_VID, ELC_PID, "AW-ELC chassis controller")
    except Exception:
        close_fds(fds)
        raise
    return fds


def close_fds(fds: dict) -> None:
    """Close descriptors from open_fds. A no-op for cached ones - the cache owns
    those, and closing them here would break the next call."""
    if _cache_enabled:
        fds.clear()
        return
    for fd in fds.values():
        try:
            os.close(fd)
        except OSError:
            pass
    fds.clear()
