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

from . import apiv4, apiv5, device, hardware, keymap, layout, state, term

_ZONE_PROBE_COLOUR = (255, 255, 255)
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
            apiv4.solid(fds["elc"], _ZONE_PROBE_COLOUR, [zone_id])
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


#: Colours the mapping step uses. Everything dark except the key being
#: considered, so there is never any doubt which LED is being talked about.
PROBE_COLOUR = (255, 0, 0)      # the candidate, while you adjust it
CONFIRMED_COLOUR = (0, 180, 0)  # saved
DARK = (0, 0, 0)

#: Controls, shown to the user and used by the dispatch below. One definition,
#: so the help text cannot drift from the behaviour.
_CONTROLS = (
    ("up / down", "choose which key you are mapping"),
    ("left / right", "move the light until it lands on that key"),
    ("PgUp / PgDn", "move the light by ten"),
    ("enter", "that's it - save this key (turns green)"),
    ("s", "skip: this machine has no such key"),
    ("u", "unassign the key shown"),
    ("q", "finish and save"),
)


def assign_leds(grid, paint, read_key, echo, max_index=_MAX_INDEX,
                assigned=None, on_save=None):
    """Name a key, light a guess, let the user walk the light onto it.

    This direction round matters. The user knows which key they are looking at
    and has no idea what its LED index is, so the terminal names the key - "Row
    3 - Q" - and the *index* is what the arrows adjust. Asking "which key is
    this LED?" instead makes them hunt for a name to type, and no name they
    guess is guaranteed to match what the renderer expects.

    The guess is the last confirmed index plus one, which is right almost every
    time because the strip runs in reading order. It also self-corrects: once
    the user fixes an offset, every later guess inherits it, so a keyboard whose
    indices start at 3 costs one correction rather than eighty-five.

    Everything is dark except the candidate, which is red, and keys already
    saved, which are green. Only the LEDs that actually change are repainted -
    a full 200-LED repaint per keypress visibly flickers.

    ``paint``, ``read_key`` and ``echo`` are injected so the flow can be driven
    by a scripted key sequence against a fake device, with no terminal.
    ``on_save`` is called after each confirmation, so progress survives a crash
    or a closed window.

    Returns ``{key_name: led_index}``.
    """
    keys = [(row_index, name)
            for row_index, row in enumerate(grid.rows)
            for name, _ in row]
    assigned = dict(assigned or {})
    position = 0
    candidate = None
    lit = None

    def guess():
        return min(max_index, max(assigned.values()) + 1) if assigned else 0

    while True:
        row_index, name = keys[position]
        if candidate is None:
            candidate = assigned.get(name, guess())

        # Repaint only what changed: restore whatever was lit, then light the
        # candidate red.
        if lit != candidate:
            changes = []
            if lit is not None and lit != candidate:
                was = next((n for n, i in assigned.items() if i == lit), None)
                changes.append((lit,) + (CONFIRMED_COLOUR if was else DARK))
            changes.append((candidate,) + PROBE_COLOUR)
            paint(changes)
            lit = candidate

        echo("")
        echo(f"  Row {row_index + 1}   key {position + 1} of {len(keys)}"
             f"   ({len(assigned)} saved)")
        echo("")
        echo(f"      Find this key:   >>>  {layout.label_for(name)}  <<<")
        echo("")
        echo(f"      The RED light is on LED {candidate}."
             + (f"   [saved as {assigned[name]}]" if name in assigned else ""))
        echo("      Is it on that key? enter=yes   left/right=move it")

        pressed = read_key()
        if pressed in ("", "q", term.ESCAPE, term.INTERRUPT):
            break

        if pressed == term.UP:
            position = max(0, position - 1)
            candidate = None
        elif pressed == term.DOWN:
            position = min(len(keys) - 1, position + 1)
            candidate = None
        elif pressed == term.LEFT:
            candidate = max(0, candidate - 1)
        elif pressed == term.RIGHT:
            candidate = min(max_index, candidate + 1)
        elif pressed == term.PAGE_UP:
            candidate = max(0, candidate - 10)
        elif pressed == term.PAGE_DOWN:
            candidate = min(max_index, candidate + 10)
        elif pressed == term.ENTER:
            assigned[name] = candidate
            paint([(candidate,) + CONFIRMED_COLOUR])
            lit = None
            if on_save:
                on_save(dict(assigned))
            if position + 1 >= len(keys):
                echo("\n  that was the last key - finishing.")
                break
            position += 1
            candidate = None
        elif pressed == "s":
            if position + 1 >= len(keys):
                break
            position += 1
            candidate = None
        elif pressed == "u":
            if assigned.pop(name, None) is not None:
                echo(f"  unassigned {name}")
                if on_save:
                    on_save(dict(assigned))
                lit = None
        else:
            echo(f"  '{pressed}' does nothing here")

    return assigned


def _choose_grid(echo) -> "layout.Layout":
    """Pick the starting layout for this machine.

    There is deliberately no "type the keys out yourself" option. The whole
    difficulty the wizard exists to solve is that the user does not know these
    names - asking them to produce eighty-five of them is the least usable
    thing it could do. A template also carries real column positions, which
    naming cannot express: a key that spans two columns, or the gap before the
    media column, is invisible in a list of names and the diagonal blend reads
    exactly those columns.

    A machine whose keyboard differs from the template still works: keys it does
    not have get skipped, and the saved keymap records only what was confirmed.
    """
    echo("")
    echo("Keyboard layout")
    echo("-" * 15)
    echo("The wizard needs the shape of your keyboard before it can ask which")
    echo("LED belongs to which key.")
    echo("")
    echo("  1. Use the built-in layout (recommended)")
    echo("  2. Start from another keymap file")

    while True:
        choice = _ask("\n  Choose [1-2]: ", "1")
        if choice in ("1", ""):
            return layout.Layout.from_keymap(keymap.load_file(keymap.SHIPPED_KEYMAP))
        if choice == "2":
            path = _ask("  Path to a keymap file: ")
            if not path:
                continue
            try:
                return layout.Layout.from_keymap(keymap.load_file(os.path.expanduser(path)))
            except (keymap.KeymapError, layout.LayoutError, OSError) as exc:
                echo(f"  cannot use that file: {exc}")
                continue
        echo("  please choose 1 or 2")


def create_new() -> int:
    print()
    print("Create New KeyMap")
    print("-" * 17)
    print(f"Detected machine: {hardware.describe()}")
    model_name = _ask(f"  Model name [{hardware.model()}]: ", hardware.model())

    zones = {}
    if _yes("\nProbe the chassis zones? (recommended on a new model)",
            default=not keymap.has_user_keymap()):
        zones = _probe_zones()

    try:
        grid = _choose_grid(print)
    except (keymap.KeymapError, layout.LayoutError) as exc:
        print(f"cannot build a layout: {exc}", file=sys.stderr)
        return 1

    print()
    print("Mapping LEDs to keys")
    print("-" * 20)
    print("Every light goes out. One key at a time, the wizard names a key and")
    print("lights its best guess in RED - walk the light onto that key with the")
    print("arrows, then press enter and it turns GREEN and is saved.")
    print()
    for control, description in _CONTROLS:
        print(f"  {control:13s} {description}")
    print()
    _ask("  Press Enter when you are ready. ")

    # Resume: keys already confirmed keep their light and are not asked again.
    existing = {}
    if keymap.has_user_keymap():
        try:
            existing = dict(keymap.load().get("key_to_index") or {})
        except keymap.KeymapError:
            existing = {}
        if existing:
            print(f"  resuming - {len(existing)} keys already mapped")

    try:
        fds = device.open_fds(["kbd"])
    except device.DeviceError as exc:
        print(f"cannot open the keyboard controller: {exc}", file=sys.stderr)
        return 1

    def paint(changes):
        apiv5.paint(fds["kbd"], list(changes))

    try:
        with term.raw_mode() as raw:
            if not raw:
                print("this step needs a terminal; run it from one", file=sys.stderr)
                return 1
            # Dark canvas, then the keys already confirmed come up green.
            apiv5.off(fds["kbd"])
            if existing:
                paint([(index,) + CONFIRMED_COLOUR for index in existing.values()])
            assigned = assign_leds(
                grid, paint, term.read_key,
                # Raw mode means no automatic carriage return.
                lambda line: print(line + "\r"),
                assigned=existing,
            )
    finally:
        try:
            apiv5.off(fds["kbd"])
        finally:
            device.close_fds(fds)

    if not assigned:
        print("nothing assigned; leaving the existing keymap alone")
        return 1

    return _save(model_name, grid, assigned, zones)


def _save(model_name, grid, assigned, zones) -> int:
    """Write the keymap file for a finished mapping."""
    # Only keys that actually got an LED are recorded. A partial mapping is
    # useful - the renderer paints the keys it knows and leaves the rest - and
    # is much better than refusing to save an hour of pointing.
    positions = grid.grid_positions()
    data = {
        "device": model_name,
        "model_slug": hardware.slug(model_name),
        "vid_pid": f"{device.KBD_VID:04x}:{device.KBD_PID:04x}",
        "key_to_index": dict(assigned),
        "index_to_key": {str(index): name for name, index in assigned.items()},
        "grid_positions": {name: positions[name] for name in assigned if name in positions},
        "total_mapped": len(assigned),
    }
    if zones:
        data["zones"] = zones

    keymap.validate(data)
    state.ensure_dirs()
    target = keymap.model_keymap_path(model_name)
    state.write_json_atomic(target, data)
    print()
    print(f"  Saved {len(assigned)} keys"
          + (f" and {len(zones)} zones" if zones else "")
          + f" -> {target}")
    missing = [name for name in grid.keys() if name not in assigned]
    if missing:
        print(f"  {len(missing)} keys have no LED yet: {', '.join(missing[:8])}"
              + (" ..." if len(missing) > 8 else ""))
        print("  Re-run the wizard to finish them.")
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
