"""The KeyMap Wizard.

Runs interactively in a terminal; the plugin launches it in one rather than
rebuilding a step-through flow in QML.

Its first question is deliberately "do you already have a keymap file?" -
importing a known-good map is instant and lossless, whereas building one by
hand means naming eighty-five keys.  Only if the user declines does it fall
through to the probe loop.
"""

from __future__ import annotations

import json
import os
import sys

from . import apiv5, device, keymap, state

_PROBE_COLOR = (255, 255, 255)
_MAX_INDEX = 199


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


def _import_flow() -> int:
    """Step 1: take an existing keymap file."""
    print()
    print("Enter the path to your keymap file (or press Enter to go back).")
    while True:
        raw = _ask("  path: ")
        if not raw:
            return 1
        path = os.path.expanduser(raw)
        if not os.path.isfile(path):
            print(f"  no such file: {path}")
            continue
        try:
            data = keymap.load_file(path)
        except keymap.KeymapError as exc:
            print(f"  not usable: {exc}")
            continue
        target = keymap.import_file(path)
        print()
        print(f"  Imported {keymap.describe(data)}")
        print(f"  Installed at {target}")
        return 0


def _build_flow(assume_yes: bool = False) -> int:
    """Step 2: build a map by lighting one LED at a time."""
    print()
    print("Interactive mapping. One key lights up at a time; type its name")
    print("(for example 'esc', 'f1', 'a', 'space', 'enter').")
    print("Commands:  [Enter] skip   'b' go back   'q' finish and save")
    print()
    if not assume_yes and not _yes("The keyboard will flash as we go. Continue?"):
        return 1

    try:
        fds = device.open_fds(["kbd"])
    except device.DeviceError as exc:
        print(f"cannot open the keyboard controller: {exc}", file=sys.stderr)
        return 1

    key_to_index: dict = {}
    grid: dict = {}
    order: list = []

    try:
        index = 0
        row, col = 0, 0
        while index <= _MAX_INDEX:
            apiv5.paint(fds["kbd"], [(index, *_PROBE_COLOR)])
            answer = _ask(f"  LED {index:3d} (row {row}, col {col}) -> ")

            if answer.lower() == "q":
                break
            if answer.lower() == "b":
                if order:
                    last = order.pop()
                    key_to_index.pop(last, None)
                    grid.pop(last, None)
                    print(f"    removed {last!r}")
                index = max(0, index - 1)
                continue
            if not answer:
                index += 1
                col += 1
                continue

            name = answer.lower()
            if name in key_to_index:
                print(f"    {name!r} is already mapped to LED {key_to_index[name]}")
                continue
            key_to_index[name] = index
            grid[name] = {"row": row, "col": col}
            order.append(name)
            index += 1
            col += 1
    finally:
        try:
            apiv5.off(fds["kbd"])
        finally:
            device.close_fds(fds)

    if not key_to_index:
        print("nothing mapped; leaving the existing keymap alone")
        return 1

    print()
    print("Now set the row breaks so the gradient knows the physical layout.")
    print("Enter the key that STARTS each row after the first, comma separated")
    print("(for example: 'tab, capslock, shift, ctrl'). Enter to keep one row.")
    breaks = [name.strip().lower() for name in _ask("  row starts: ").split(",") if name.strip()]
    if breaks:
        current_row, current_col = 0, 0
        for name in order:
            if name in breaks:
                current_row += 1
                current_col = 0
            grid[name] = {"row": current_row, "col": current_col}
            current_col += 1

    data = {
        "device": _ask("  device name [Alienware]: ", "Alienware"),
        "vid_pid": f"{device.KBD_VID:04x}:{device.KBD_PID:04x}",
        "key_to_index": key_to_index,
        "index_to_key": {str(v): k for k, v in key_to_index.items()},
        "grid_positions": grid,
        "total_mapped": len(key_to_index),
    }
    keymap.validate(data)
    state.ensure_dirs()
    target = keymap.user_keymap_path()
    state.write_json_atomic(target, data)
    print()
    print(f"  Saved {len(key_to_index)} keys -> {target}")
    return 0


def run(assume_yes: bool = False) -> int:
    print("=" * 62)
    print(" AlienFX KeyMap Wizard")
    print("=" * 62)
    print()
    print("A keymap tells the gradient which LED belongs to which key, and")
    print("where that key sits on the keyboard. Layouts differ between")
    print("machines and regions, so this laptop may need its own.")

    if keymap.has_user_keymap():
        print()
        print(f"A keymap is already installed at {keymap.user_keymap_path()}")
        if not _yes("Replace it?", default=False):
            return 0

    print()
    if _yes("Do you already have a keymap file you'd like to load?", default=True):
        result = _import_flow()
        if result == 0:
            return 0
        print("  (falling through to interactive mapping)")

    return _build_flow(assume_yes=assume_yes)
