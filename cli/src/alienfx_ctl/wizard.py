"""The KeyMap Wizard.

Runs interactively in a terminal; the popup launches it in one rather than
rebuilding a step-through flow in QML.

Two things it exists for. Obviously, naming the LEDs so a per-key gradient knows
where each key sits. Less obviously, capturing everything about *this* machine
that the code should not assume: its model, how many chassis zones it has and at
which protocol ids. Those differ between Alienware models, so they live in the
keymap file rather than in the source.
"""

from __future__ import annotations

import json
import os
import sys

from . import apiv4, apiv5, device, hardware, keymap, state

_PROBE_COLOR = (255, 255, 255)
_MAX_INDEX = 199

#: Chassis light ids worth probing on an unknown model. The reference machine
#: answers on 0, 2 and 4; the rest of the space is addressable but drives
#: nothing there, and another model may differ.
_ZONE_PROBE_IDS = tuple(range(10))

_RULE = "=" * 64


def _ask(prompt: str, default: str = "") -> str:
    try:
        answer = input(prompt).strip()
    except EOFError:
        return default
    return answer or default


def _yes(prompt: str, default: bool = True) -> bool:
    suffix = " [Y/n] " if default else " [y/N] "
    while True:
        answer = _ask(prompt + suffix).lower()
        if not answer:
            return default
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        print("  please answer y or n")


def _status() -> None:
    print(f"  Machine  : {hardware.describe()}")
    print(f"  Keymaps  : {state.keymap_dir()}")
    print(f"  Expected : {hardware.keymap_filename()}")
    if keymap.has_user_keymap():
        try:
            data = keymap.load_file(keymap.user_keymap_path())
            print(f"  Installed: {keymap.describe(data)}")
            print(f"             {keymap.user_keymap_path()}")
        except keymap.KeymapError as exc:
            print(f"  Installed: present but unreadable ({exc})")
    else:
        print("  Installed: none for this machine - the shipped default is in use")
    if not hardware.looks_supported():
        print()
        print("  Note: this does not look like an Alienware machine. The wizard will")
        print("        still run, but the lighting controllers may not be present.")


# ------------------------------------------------------------------ option 1

def load_existing() -> int:
    """Offer every keymap we can find, plus a path prompt."""
    print()
    print("Load Existing KeyMap File")
    print("-" * 25)

    found = [(path, data) for path, data in keymap.discover_keymaps()]
    if found:
        print()
        print("Found these keymap files:")
        for number, (path, data) in enumerate(found, start=1):
            print(f"  {number}. {keymap.describe(data)}")
            print(f"     {path}")
        print(f"  {len(found) + 1}. Enter a path myself")
        print("  0. Back")
        choice = _ask(f"\nChoose [0-{len(found) + 1}]: ")
        if choice in ("0", ""):
            return 1
        if choice.isdigit() and 1 <= int(choice) <= len(found):
            path = found[int(choice) - 1][0]
            return _install(path)
        if not (choice.isdigit() and int(choice) == len(found) + 1):
            print("  not a valid choice")
            return 1
    else:
        print()
        print("No keymap files found in the usual places.")

    print()
    print("Enter the path to a keymap file (Enter to go back).")
    while True:
        raw = _ask("  path: ")
        if not raw:
            return 1
        path = os.path.expanduser(raw)
        if not os.path.isfile(path):
            print(f"  no such file: {path}")
            continue
        return _install(path)


def _install(path: str) -> int:
    try:
        data = keymap.load_file(path)
    except keymap.KeymapError as exc:
        print(f"  not usable: {exc}")
        return 1
    target = keymap.import_file(path)
    print()
    print(f"  Loaded  {keymap.describe(data)}")
    print(f"  Saved   {target}")
    return 0


# ------------------------------------------------------------------ option 2

def _probe_zones() -> dict:
    """Find which chassis zones this machine actually has.

    Lights one candidate id at a time and asks what came on. Needed because
    another model may have fewer zones, or address them differently, and
    guessing produces a plugin that writes to nothing.
    """
    print()
    print("Chassis zones")
    print("-" * 13)
    print("Each candidate light will come on WHITE one at a time. Type what lit up:")
    print("  kbd-none / tpd (touchpad) / logo (lid) / pbtn (power button)")
    print("  or a name of your own, Enter to skip, 'q' to stop probing.")
    print()

    try:
        fds = device.open_fds(["tpd"])
    except device.DeviceError as exc:
        print(f"  cannot reach the chassis controller: {exc}")
        print("  falling back to the built-in zone map.")
        return {}

    found: dict = {}
    try:
        for zone_id in _ZONE_PROBE_IDS:
            apiv4.solid(fds["elc"], _PROBE_COLOR, [zone_id])
            answer = _ask(f"  light id 0x{zone_id:02x} -> ")
            apiv4.off(fds["elc"], [zone_id])
            if answer.lower() == "q":
                break
            if not answer:
                continue
            name = answer.strip().lower()
            found.setdefault(name, []).append(zone_id)
    finally:
        try:
            apiv4.off(fds["elc"], list(_ZONE_PROBE_IDS))
        finally:
            device.close_fds(fds)

    if found:
        print()
        print("  Zones recorded:")
        for name, ids in sorted(found.items()):
            print(f"    {name:8s} {', '.join(f'0x{i:02x}' for i in ids)}")
    return found


def _probe_keys() -> tuple:
    """Light one LED at a time and record what the user names."""
    print()
    print("Keyboard keys")
    print("-" * 13)
    print("One key lights up at a time. Type its name, for example:")
    print("  esc  f1  a  space  enter  lshift  backslash")
    print()
    print("  [Enter] skip this LED (not every index is a real key)")
    print("  b       go back one")
    print("  q       finish and save")
    print()

    try:
        fds = device.open_fds(["kbd"])
    except device.DeviceError as exc:
        print(f"cannot open the keyboard controller: {exc}", file=sys.stderr)
        return {}, []

    key_to_index: dict = {}
    order: list = []
    try:
        index = 0
        while index <= _MAX_INDEX:
            apiv5.paint(fds["kbd"], [(index, *_PROBE_COLOR)])
            answer = _ask(f"  LED {index:3d}  ({len(order)} named) -> ")

            if answer.lower() == "q":
                break
            if answer.lower() == "b":
                if order:
                    last = order.pop()
                    key_to_index.pop(last, None)
                    print(f"    removed {last!r}")
                    index = key_to_index[order[-1]] + 1 if order else 0
                else:
                    index = 0
                continue
            if not answer:
                index += 1
                continue

            name = answer.lower()
            if name in key_to_index:
                print(f"    {name!r} is already LED {key_to_index[name]}")
                continue
            key_to_index[name] = index
            order.append(name)
            index += 1
    finally:
        try:
            apiv5.off(fds["kbd"])
        finally:
            device.close_fds(fds)
    return key_to_index, order


def create_new() -> int:
    print()
    print("Create New KeyMap")
    print("-" * 17)
    print(f"Detected machine: {hardware.describe()}")
    model_name = _ask(f"  Model name [{hardware.model()}]: ", hardware.model())

    zones = {}
    if _yes("\nProbe the chassis zones? (recommended on a new model)", default=not keymap.has_user_keymap()):
        zones = _probe_zones()

    key_to_index, order = _probe_keys()
    if not key_to_index:
        print("nothing named; leaving the existing keymap alone")
        return 1

    print()
    print("Row layout")
    print("-" * 10)
    print("The gradient needs to know where each key sits. Name the key that")
    print("STARTS each row after the first, comma separated, for example:")
    print("  tab, capslock, shift, ctrl")
    print("Press Enter to treat the whole keyboard as one row.")
    breaks = [name.strip().lower() for name in _ask("  row starts: ").split(",") if name.strip()]

    grid: dict = {}
    row = col = 0
    for name in order:
        if name in breaks:
            row += 1
            col = 0
        grid[name] = {"row": row, "col": col}
        col += 1

    data = {
        "device": model_name,
        "model_slug": hardware.slug(model_name),
        "vid_pid": f"{device.KBD_VID:04x}:{device.KBD_PID:04x}",
        "key_to_index": key_to_index,
        "index_to_key": {str(v): k for k, v in key_to_index.items()},
        "grid_positions": grid,
        "total_mapped": len(key_to_index),
    }
    if zones:
        data["zones"] = zones

    keymap.validate(data)
    state.ensure_dirs()
    target = keymap.model_keymap_path(model_name)
    state.write_json_atomic(target, data)
    print()
    print(f"  Saved {len(key_to_index)} keys"
          + (f" and {len(zones)} zones" if zones else "")
          + f" -> {target}")
    return 0


# --------------------------------------------------------------------- menu

def run(assume_yes: bool = False) -> int:
    print(_RULE)
    print(" AlienFX KeyMap Wizard")
    print(_RULE)
    print()
    print("A keymap records which LED belongs to which key, where that key sits,")
    print("and which chassis zones this machine has. Layouts differ between")
    print("models and regions, so each machine keeps its own.")
    print()
    _status()

    while True:
        print()
        print("  1. Load Existing KeyMap File")
        print("  2. Create New KeyMap")
        print("  3. Exit")
        choice = _ask("\nChoose [1-3]: ")

        if choice == "1":
            if load_existing() == 0:
                return 0
        elif choice == "2":
            if create_new() == 0:
                return 0
        elif choice in ("3", "q", "quit", "exit", ""):
            print("  nothing changed")
            return 0
        else:
            print("  please choose 1, 2 or 3")
