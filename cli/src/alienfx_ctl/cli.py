"""Command line interface.

Deliberately thin: parse arguments, mutate state, hand off to the engine.  The
Quickshell plugin drives this same surface, so anything the UI can do is also
doable by hand - which makes the whole thing debuggable without the shell.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys

from . import __version__, apiv5, colors, device, engine, gradient, keymap, lock, palette, state


def _fail(message: str, code: int = 2) -> int:
    print(f"alienfx-ctl: {message}", file=sys.stderr)
    return code


def _parse_zones(value, st=None):
    """Resolve a --zones value into a list of zone names."""
    if not value or value == "all":
        return list(device.ZONES)
    if value == "selected":
        if st and not st.get("zonesync"):
            return [st.get("selected_zone", "kbd")]
        return list(device.ZONES)
    wanted = [part.strip() for part in str(value).split(",") if part.strip()]
    unknown = [zone for zone in wanted if zone not in device.ZONES]
    if unknown:
        raise ValueError(f"unknown zone(s): {', '.join(unknown)}; valid: {', '.join(device.ZONES)}")
    return wanted


def _bool_word(value) -> bool:
    text = str(value).strip().lower()
    if text in ("on", "true", "yes", "1", "enable", "enabled"):
        return True
    if text in ("off", "false", "no", "0", "disable", "disabled"):
        return False
    raise ValueError(f"expected on/off, got {value!r}")


def _print_plan(work) -> None:
    print(f"effect: {work['effect']}  zones: {', '.join(work['zones'])}")
    for zone, rgb in sorted(work["elc"].items()):
        print(f"  {zone:5s} -> #{colors.to_hex(rgb)}")
    if work.get("power_programmed"):
        print(f"  pbtn  -> power-state blocks programmed (#{work['power_programmed']})")
    if work["kbd_leds"] is not None:
        leds = work["kbd_leds"]
        first = colors.to_hex(leds[0][1:]) if leds else "-"
        last = colors.to_hex(leds[-1][1:]) if leds else "-"
        print(f"  kbd   -> {len(leds)} keys, #{first} .. #{last}")
    elif work["kbd_effect"] is not None:
        spec = work["kbd_effect"]
        print(f"  kbd   -> firmware effect {spec['code']} #{colors.to_hex(spec['rgb'])} tempo {spec['tempo']}")
    elif work["kbd_solid"] is not None:
        print(f"  kbd   -> #{colors.to_hex(work['kbd_solid'])} (all keys)")


def _apply(st, zones, args, save=True):
    if getattr(args, "dry_run", False):
        _print_plan(engine.plan(st, zones))
        return 0

    fast = getattr(args, "fast", False)
    try:
        # A dropped drag frame is invisible - the next one supersedes it - but a
        # deliberate action should wait its turn rather than vanish.
        with lock.hardware_lock(wait=0.0 if fast else 3.0, drop_if_busy=fast):
            work = engine.apply(st, zones,
                                persist=getattr(args, "persist", False),
                                fast=fast)
            if save and not getattr(args, "no_save", False):
                state.save_state(st)
    except lock.Busy:
        if fast:
            return 0
        return _fail("hardware is busy; try again", 1)

    if getattr(args, "verbose", False):
        _print_plan(work)
    return 0


# ---------------------------------------------------------------- commands

def cmd_state(args) -> int:
    st = state.load_state()
    if args.json:
        payload = dict(st)
        payload["profiles"] = state.list_profiles()
        payload["current_profile"] = state.current_profile()
        payload["has_keymap"] = keymap.has_user_keymap()
        # The plugin needs to tell "no CLI" apart from "CLI present but the
        # udev rule was never installed" - they look identical otherwise, and
        # only the second one means the lights silently do nothing.
        payload["devices_ok"] = _devices_ready()
        payload["devices"] = _devices_report()
        payload["theme"] = palette.theme_name()
        payload["effects"] = list(engine.EFFECTS)
        payload["zone_names"] = list(device.ZONES)
        try:
            first, second = engine.resolve_anchors(st)
            payload["anchors"] = [colors.to_hex(first), colors.to_hex(second)]
        except Exception:
            payload["anchors"] = None
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    print(f"theme       : {palette.theme_name()}")
    print(f"themesync   : {'on' if st['themesync'] else 'off'}")
    print(f"zonesync    : {'on' if st['zonesync'] else 'off'}")
    print(f"effect      : {engine.effective_effect(st)}")
    print(f"brightness  : {st['brightness']} ({round(st['brightness'] / 255 * 100)}%)")
    print(f"profile     : {state.current_profile() or '(none)'}")
    print(f"keymap      : {'user' if keymap.has_user_keymap() else 'shipped default'}")
    for zone in device.ZONES:
        print(f"  {zone:5s} -> #{colors.to_hex(engine.zone_color(st, zone))}")
    return 0


def cmd_set(args) -> int:
    """The single mutate-and-apply entry point the plugin uses."""
    st = state.load_state()

    themesync_turned_on = False
    if args.themesync is not None:
        state.set_themesync(_bool_word(args.themesync))
        st["themesync"] = state.themesync_enabled()
        themesync_turned_on = st["themesync"]
    if args.zonesync is not None:
        st["zonesync"] = _bool_word(args.zonesync)
    if args.effect is not None:
        if args.effect not in engine.EFFECTS:
            return _fail(f"unknown effect {args.effect!r}; valid: {', '.join(engine.EFFECTS)}")
        st["effect"] = args.effect
    if args.axis is not None:
        if args.axis not in gradient.AXES:
            return _fail(f"unknown axis {args.axis!r}; valid: {', '.join(gradient.AXES)}")
        st["axis"] = args.axis
    if args.speed is not None:
        st["speed"] = args.speed
    if args.brightness is not None:
        st["brightness"] = colors.parse_brightness(args.brightness)
    if args.saturation is not None:
        st["saturation"] = max(0.0, float(args.saturation))
    if args.min_saturation is not None:
        st["min_saturation"] = max(0.0, min(1.0, float(args.min_saturation)))

    if args.select is not None:
        if args.select not in device.ZONES:
            return _fail(f"unknown zone {args.select!r}")
        st["selected_zone"] = args.select

    # Resolved after --select so a zone change targets the newly selected zone
    # rather than the previous one.
    zones = _parse_zones(args.zones, st)

    # ThemeSync drives the whole chassis from the theme, so switching it on has
    # to repaint every zone - not just whichever one the cursor happened to be
    # sitting on.
    if themesync_turned_on:
        zones = list(device.ZONES)

    if args.color is not None:
        rgb = colors.parse_color(args.color, palette=_safe_palette())
        hexed = colors.to_hex(rgb)
        # With zones synced there is one colour for the whole chassis, so write
        # it to every zone; that way unsyncing later keeps the current look
        # instead of snapping back to stale per-zone values.
        targets = device.ZONES if st.get("zonesync") else zones
        for zone in targets:
            st["zones"].setdefault(zone, {})["color"] = hexed
        if len(zones) == 1:
            st["selected_zone"] = zones[0]

        # Gradient derives every zone from the two anchors, so a colour picked
        # for one zone would be computed away and the pick would look ignored.
        # Solid is the only effect that can express per-zone colours, so honour
        # the pick by switching to it. With zones synced there is a single
        # colour and gradient can use it as the near anchor, so leave it alone.
        if (not st.get("zonesync") and args.effect is None
                and st.get("effect") == "gradient"):
            st["effect"] = "solid"

    # Any manual colour or effect change is a departure from the theme, so it
    # implies ThemeSync off unless the user explicitly asked for it on.
    if (args.color is not None or args.effect is not None) and args.themesync is None:
        if st["themesync"]:
            state.set_themesync(False)
            st["themesync"] = False

    # A bare --select only moves the UI cursor. Persist it and stop; repainting
    # identical colours would just add latency to every zone click.
    if (args.select is not None and args.color is None and args.effect is None
            and args.brightness is None and args.themesync is None
            and args.zonesync is None and args.axis is None
            and args.speed is None and args.saturation is None):
        state.save_state(st)
        return 0

    return _apply(st, list(device.ZONES) if st.get("zonesync") else zones, args)


def _safe_palette():
    try:
        return palette.load_palette()
    except palette.PaletteError:
        return None


def cmd_solid(args) -> int:
    st = state.load_state()
    st["effect"] = "solid"
    zones = _parse_zones(args.zones, st)
    if args.color:
        hexed = colors.to_hex(colors.parse_color(args.color, palette=_safe_palette()))
        for zone in zones:
            st["zones"].setdefault(zone, {})["color"] = hexed
        if len(zones) < len(device.ZONES):
            st["zonesync"] = False
    if args.brightness is not None:
        st["brightness"] = colors.parse_brightness(args.brightness)
    if st["themesync"]:
        state.set_themesync(False)
        st["themesync"] = False
    return _apply(st, zones, args)


def cmd_off(args) -> int:
    st = state.load_state()
    zones = _parse_zones(args.zones, st)
    st["effect"] = "off"
    if st["themesync"]:
        state.set_themesync(False)
        st["themesync"] = False
    return _apply(st, zones, args)


def cmd_effect(args) -> int:
    st = state.load_state()
    if args.name not in engine.EFFECTS:
        return _fail(f"unknown effect {args.name!r}; valid: {', '.join(engine.EFFECTS)}")
    st["effect"] = args.name
    zones = _parse_zones(args.zones, st)
    if args.color:
        hexed = colors.to_hex(colors.parse_color(args.color, palette=_safe_palette()))
        for zone in (device.ZONES if st.get("zonesync") else zones):
            st["zones"].setdefault(zone, {})["color"] = hexed
    if args.brightness is not None:
        st["brightness"] = colors.parse_brightness(args.brightness)
    if args.speed:
        st["speed"] = args.speed
    if args.name != "gradient" and st["themesync"]:
        state.set_themesync(False)
        st["themesync"] = False
    return _apply(st, zones, args)


def cmd_theme(args) -> int:
    if args.theme_command == "apply":
        st = state.load_state()
        if not st["themesync"] and not args.force:
            # The theme-set hook calls this on every switch; staying quiet when
            # ThemeSync is off is what makes the hook safe to leave installed.
            if args.quiet:
                return 0
            print("themesync is off; nothing to do (use --force to apply anyway)")
            return 0
        # 'theme apply' means paint from the theme by definition, so force the
        # theme anchors for this call. Not persisted: save_state drops the key,
        # and the flag file remains the source of truth.
        st["themesync"] = True
        st["effect"] = "gradient"
        try:
            return _apply(st, list(device.ZONES), args)
        except palette.PaletteError as exc:
            return _fail(str(exc), 1)
    if args.theme_command == "show":
        try:
            first, second = palette.anchors()
        except palette.PaletteError as exc:
            return _fail(str(exc), 1)
        print(f"theme     : {palette.theme_name()}")
        print(f"primary   : #{colors.to_hex(first)}")
        print(f"secondary : #{colors.to_hex(second)}")
        kbd = palette.keyboard_rgb()
        print(f"keyboard.rgb : {'#' + colors.to_hex(kbd) if kbd else '(none)'}")
        return 0
    return _fail("unknown theme subcommand")


def cmd_themesync(args) -> int:
    if args.value == "status":
        print("on" if state.themesync_enabled() else "off")
        return 0
    enabled = _bool_word(args.value)
    state.set_themesync(enabled)
    st = state.load_state()
    if enabled:
        st["effect"] = "gradient"
    return _apply(st, list(device.ZONES), args)


def cmd_zonesync(args) -> int:
    st = state.load_state()
    st["zonesync"] = _bool_word(args.value)
    return _apply(st, list(device.ZONES), args)


def cmd_stream(args) -> int:
    """Apply many changes through one long-lived process.

    Reads newline-delimited commands from stdin, each one exactly the arguments
    you would pass on the command line, and applies them against descriptors
    that stay open. That removes the two costs that dominate a one-shot
    invocation - ~50ms of interpreter and imports, and ~52ms to open the chassis
    node - which is the difference between a colour drag that steps and one that
    follows the pointer.

    The popup starts this when it opens and closes stdin when it closes, so
    nothing is left running in the background.

    Line protocol: a command's own output (if it has any) is written first, then
    a single status line terminates the response - `ok`, or `err <message>`. A
    reader should consume lines until that terminator rather than assuming one
    line per command.
    """
    parser = build_parser()
    device.enable_cache()
    try:
        while True:
            line = sys.stdin.readline()
            if not line:
                break  # stdin closed: the popup went away
            text = line.strip()
            if not text or text.startswith("#"):
                continue
            if text in ("quit", "exit"):
                break
            try:
                argv = shlex.split(text)
            except ValueError as exc:
                print(f"err bad quoting: {exc}", flush=True)
                continue
            # A streamed command must never be able to nest another stream.
            if argv and argv[0] == "stream":
                print("err stream cannot nest", flush=True)
                continue
            try:
                namespace = parser.parse_args(argv)
                code = namespace.func(namespace)
                print("ok" if code == 0 else f"err exit {code}", flush=True)
            except SystemExit:
                # argparse exits on a bad command; in a stream that must not
                # take the whole process down with it.
                print("err invalid command", flush=True)
            except Exception as exc:
                print(f"err {exc}", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        device.close_cache()
    return 0


def cmd_commit(args) -> int:
    """Re-apply current state durably, programming what the fast path skipped.

    Interactive changes all go out --fast so nothing the user is driving pays
    for the power button's ~2s NVRAM walk. This is what the UI calls once the
    user has settled, so that colour still survives a power transition.
    """
    st = state.load_state()
    return _apply(st, list(device.ZONES), args)


def cmd_restore(args) -> int:
    """Re-apply live state. Used by the systemd unit at login and after resume."""
    st = state.load_state()
    try:
        return _apply(st, list(device.ZONES), args, save=False)
    except device.DeviceError as exc:
        # A restore that runs before the hidraw ACL lands must not fail the
        # unit; the user can re-apply, and next login will work.
        return _fail(str(exc), 0 if args.tolerant else 1)


def cmd_profile(args) -> int:
    if args.profile_command == "list":
        names = state.list_profiles()
        current = state.current_profile()
        if args.json:
            print(json.dumps({"profiles": names, "current": current}, indent=2))
            return 0
        if not names:
            print("(no profiles)")
        for name in names:
            print(f"{'*' if name == current else ' '} {name}")
        return 0

    if args.profile_command == "save":
        st = state.load_state()
        st["themesync_at_save"] = state.themesync_enabled()
        path = state.save_profile(args.name, st)
        state.set_current_profile(args.name)
        print(f"saved profile {args.name!r} -> {path}")
        return 0

    if args.profile_command == "load":
        loaded = state.load_profile(args.name)
        want_sync = bool(loaded.pop("themesync_at_save", False))
        state.set_themesync(want_sync)
        loaded["themesync"] = want_sync
        state.save_state(loaded)
        state.set_current_profile(args.name)
        print(f"loaded profile {args.name!r}")
        return _apply(loaded, list(device.ZONES), args, save=False)

    if args.profile_command == "rename":
        path = state.rename_profile(args.name, args.new_name)
        print(f"renamed {args.name!r} -> {args.new_name!r} ({path})")
        return 0

    if args.profile_command == "delete":
        state.delete_profile(args.name)
        if state.current_profile() == args.name:
            state.set_current_profile(None)
        print(f"deleted profile {args.name!r}")
        return 0

    if args.profile_command == "current":
        print(state.current_profile() or "")
        return 0

    return _fail("unknown profile subcommand")


def _devices_report():
    """Per-controller discovery and writability, for diagnostics and the UI."""
    report = {}
    for key, label, vid, pid in (
        ("elc", "AW-ELC chassis", device.ELC_VID, device.ELC_PID),
        ("kbd", "keyboard", device.KBD_VID, device.KBD_PID),
    ):
        path = device.find_node(vid, pid)
        report[key] = {
            "label": label,
            "path": path,
            "found": path is not None,
            "writable": bool(path) and os.access(path, os.R_OK | os.W_OK),
        }
    return report


def _devices_ready() -> bool:
    return all(entry["writable"] for entry in _devices_report().values())


def cmd_devices(args) -> int:
    """Diagnostics: what we found, and whether we can write to it."""
    print(f"{'node':10s} {'vid:pid':12s} role")
    roles = {
        (device.ELC_VID, device.ELC_PID): "AW-ELC chassis (tpd/logo/pbtn)",
        (device.KBD_VID, device.KBD_PID): "keyboard (per-key)",
    }
    for node, vid, pid in device.iter_nodes():
        role = roles.get((vid, pid), "")
        print(f"/dev/{node:5s} {vid:04x}:{pid:04x}    {role}")
    print()
    for label, vid, pid in (
        ("chassis ", device.ELC_VID, device.ELC_PID),
        ("keyboard", device.KBD_VID, device.KBD_PID),
    ):
        path = device.find_node(vid, pid)
        if not path:
            print(f"{label}: NOT FOUND")
            continue
        writable = os.access(path, os.R_OK | os.W_OK)
        print(f"{label}: {path} {'writable' if writable else 'NOT writable (udev rule missing?)'}")
    return 0


def cmd_keymap(args) -> int:
    if args.keymap_command == "status":
        if keymap.has_user_keymap():
            data = keymap.load_file(keymap.user_keymap_path())
            print(f"user keymap: {keymap.user_keymap_path()} ({keymap.describe(data)})")
            return 0
        print("no user keymap; using the shipped default")
        return 1

    if args.keymap_command == "import":
        path = keymap.import_file(args.path)
        print(f"imported keymap -> {path}")
        return 0

    if args.keymap_command == "show":
        data = keymap.load()
        print(keymap.describe(data))
        return 0

    if args.keymap_command == "wizard":
        from . import wizard as wizard_module
        return wizard_module.run(assume_yes=args.yes)

    return _fail("unknown keymap subcommand")


# ---------------------------------------------------------------- parser

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="alienfx-ctl",
        description="Control the RGB lighting zones on a supported Alienware laptop.",
    )
    parser.add_argument("--version", action="version", version=f"alienfx-ctl {__version__}")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--dry-run", action="store_true", help="show what would be sent, write nothing")
    common.add_argument("--verbose", "-v", action="store_true", help="print the applied colours")
    common.add_argument("--no-save", action="store_true", help="apply without updating saved state")
    common.add_argument("--persist", action="store_true",
                        help="also write chassis colours to NVRAM so they survive a cold boot")
    common.add_argument("--fast", action="store_true",
                        help="skip power-button state programming (for live drags)")

    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("state", parents=[common], help="show current state")
    p.add_argument("--json", action="store_true", help="machine-readable output (used by the plugin)")
    p.set_defaults(func=cmd_state)

    p = sub.add_parser("set", parents=[common], help="change settings and apply (the plugin's entry point)")
    p.add_argument("--color", "-c", help="colour: RRGGBB, #RRGGBB, 'r,g,b', a name, or @themekey")
    p.add_argument("--zones", "-z", default="selected", help="comma list, 'all', or 'selected'")
    p.add_argument("--brightness", "-b", help="0-255 or a percentage like 10%%")
    p.add_argument("--saturation", type=float, help="saturation multiplier (default 1.0, a no-op)")
    p.add_argument("--min-saturation", dest="min_saturation", type=float,
                   help="saturation floor 0-1: lifts washed-out theme colours, leaves vivid ones alone")
    p.add_argument("--effect", "-e", choices=engine.EFFECTS)
    p.add_argument("--axis", choices=gradient.AXES)
    p.add_argument("--speed", "-s", choices=sorted(apiv5.SPEED_PRESETS))
    p.add_argument("--themesync", help="on/off")
    p.add_argument("--zonesync", help="on/off")
    p.add_argument("--select", help="remember this zone as the selected one")
    p.set_defaults(func=cmd_set)

    p = sub.add_parser("solid", parents=[common], help="set a flat colour")
    p.add_argument("--color", "-c", required=True)
    p.add_argument("--zones", "-z", default="all")
    p.add_argument("--brightness", "-b")
    p.set_defaults(func=cmd_solid)

    p = sub.add_parser("off", parents=[common], help="turn zones off")
    p.add_argument("--zones", "-z", default="all")
    p.set_defaults(func=cmd_off)

    p = sub.add_parser("effect", parents=[common], help="run an effect")
    p.add_argument("name", choices=engine.EFFECTS)
    p.add_argument("--zones", "-z", default="all")
    p.add_argument("--color", "-c")
    p.add_argument("--brightness", "-b")
    p.add_argument("--speed", "-s")
    p.set_defaults(func=cmd_effect)

    p = sub.add_parser("theme", parents=[common], help="theme palette sync")
    psub = p.add_subparsers(dest="theme_command", required=True)
    q = psub.add_parser("apply", parents=[common], help="paint the theme gradient now")
    q.add_argument("--force", action="store_true", help="apply even if themesync is off")
    q.add_argument("--quiet", action="store_true", help="say nothing when themesync is off (hook mode)")
    q.set_defaults(func=cmd_theme)
    q = psub.add_parser("show", help="show the anchors derived from the active theme")
    q.set_defaults(func=cmd_theme)

    p = sub.add_parser("themesync", parents=[common], help="turn theme syncing on or off")
    p.add_argument("value", help="on, off, or status")
    p.set_defaults(func=cmd_themesync)

    p = sub.add_parser("zonesync", parents=[common], help="control all zones together or individually")
    p.add_argument("value", help="on or off")
    p.set_defaults(func=cmd_zonesync)

    p = sub.add_parser("stream", parents=[common],
                       help="read commands from stdin with the devices held open")
    p.set_defaults(func=cmd_stream)

    p = sub.add_parser("commit", parents=[common],
                       help="re-apply state durably (programs power-button NVRAM)")
    p.set_defaults(func=cmd_commit)

    p = sub.add_parser("restore", parents=[common], help="re-apply saved state (login/resume)")
    p.add_argument("--tolerant", action="store_true",
                   help="exit 0 even if the hardware is not ready yet")
    p.set_defaults(func=cmd_restore)

    p = sub.add_parser("profile", parents=[common], help="named profiles")
    psub = p.add_subparsers(dest="profile_command", required=True)
    q = psub.add_parser("list", help="list profiles")
    q.add_argument("--json", action="store_true")
    q.set_defaults(func=cmd_profile)
    q = psub.add_parser("save", help="save current state as a profile")
    q.add_argument("name")
    q.set_defaults(func=cmd_profile)
    q = psub.add_parser("load", parents=[common], help="load a profile and make it persistent")
    q.add_argument("name")
    q.set_defaults(func=cmd_profile)
    q = psub.add_parser("rename", help="rename a profile")
    q.add_argument("name")
    q.add_argument("new_name")
    q.set_defaults(func=cmd_profile)
    q = psub.add_parser("delete", help="delete a profile")
    q.add_argument("name")
    q.set_defaults(func=cmd_profile)
    q = psub.add_parser("current", help="print the loaded profile name")
    q.set_defaults(func=cmd_profile)

    p = sub.add_parser("keymap", help="per-key keymap management")
    psub = p.add_subparsers(dest="keymap_command", required=True)
    q = psub.add_parser("status", help="exit 0 if a user keymap exists")
    q.set_defaults(func=cmd_keymap)
    q = psub.add_parser("show", help="describe the active keymap")
    q.set_defaults(func=cmd_keymap)
    q = psub.add_parser("import", help="install an existing keymap file")
    q.add_argument("path")
    q.set_defaults(func=cmd_keymap)
    q = psub.add_parser("wizard", help="import or build a keymap interactively")
    q.add_argument("--yes", "-y", action="store_true", help="skip confirmations")
    q.set_defaults(func=cmd_keymap)

    p = sub.add_parser("devices", help="show detected controllers and access")
    p.set_defaults(func=cmd_devices)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (colors.ColorError, gradient.GradientError, state.StateError,
            keymap.KeymapError, engine.EngineError, ValueError) as exc:
        return _fail(str(exc))
    except palette.PaletteError as exc:
        return _fail(str(exc), 1)
    except device.DeviceError as exc:
        return _fail(str(exc), 1)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
