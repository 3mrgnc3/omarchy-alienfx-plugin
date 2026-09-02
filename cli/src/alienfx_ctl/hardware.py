"""Identifying the machine we are running on.

This plugin is not m16 R2 specific by design, but a lot about RGB lighting is
model specific: how many zones there are, what they are called, which protocol
ids address them, and how many LEDs the keyboard has. Those belong to a *device
profile* rather than to the code, and the profile is keyed on what the firmware
reports about itself here.

Read from DMI, which is present on every Alienware and Dell machine and needs no
privileges.
"""

from __future__ import annotations

import os
import re

_DMI = "/sys/class/dmi/id"

#: Slug characters we allow, so a model name can safely become a filename.
_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def _dmi(name: str) -> str:
    try:
        with open(os.path.join(_DMI, name), "r", encoding="utf-8", errors="replace") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def vendor() -> str:
    return _dmi("sys_vendor")


def model() -> str:
    """The machine's model name, e.g. ``Alienware m16 R2``."""
    return _dmi("product_name") or "unknown"


def sku() -> str:
    return _dmi("product_sku")


def bios_version() -> str:
    return _dmi("bios_version")


def slug(text: str = "") -> str:
    """A filename-safe form of a model name: ``Alienware m16 R2`` -> ``m16-r2``.

    The vendor prefix is dropped because the filename already carries it
    (``alienware-<slug>-keymap.json``), and a bare ``m16-r2`` is what a user
    would think to type.
    """
    raw = (text or model()).strip().lower()
    for prefix in ("alienware ", "dell ", "alienware-", "dell-"):
        if raw.startswith(prefix):
            raw = raw[len(prefix):]
            break
    cleaned = _SLUG_STRIP.sub("-", raw).strip("-")
    return cleaned or "unknown"


def keymap_filename(text: str = "") -> str:
    """The per-model keymap filename this machine should use."""
    return f"alienware-{slug(text)}-keymap.json"


def describe() -> str:
    name, brand = model(), vendor()
    # product_name is usually already "Alienware m16 R2", so only prepend the
    # vendor when it is not already there.
    label = name if (not brand or brand.lower() in name.lower()) else f"{brand} {name}"
    label = label or "unknown machine"
    extra = []
    if sku():
        extra.append(f"sku {sku()}")
    if bios_version():
        extra.append(f"BIOS {bios_version()}")
    return f"{label}" + (f" ({', '.join(extra)})" if extra else "")


def looks_supported() -> bool:
    """Whether this machine is plausibly an Alienware with AlienFX zones.

    Only advisory - the real test is whether the controllers are found by
    vendor/product id, which is what ``device`` does.
    """
    return "alienware" in (vendor() + " " + model()).lower()
