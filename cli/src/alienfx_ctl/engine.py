"""Turning a state dictionary into light.

One entry point, ``apply``, owns the whole path: work out the colours, open
only the controllers that are needed, write them, and close.  Keeping it in one
place is what lets the CLI be a thin argument parser and the plugin be thin
QML - neither of them knows anything about HID.
"""

from __future__ import annotations

import threading

from . import apiv4, apiv5, colors, device, gradient, keymap, palette

#: Effects offered to the user.  Chassis zones can only hold a flat colour, so
#: the animated ones are keyboard-only and the chassis takes the base colour.
EFFECTS = ("gradient", "solid", "wave", "pulse", "nightrider", "off")

#: Animated effects are produced by the keyboard controller itself.
_FIRMWARE_EFFECTS = {
    "wave": (apiv5.EFFECT_WAVE, 1),
    "pulse": (apiv5.EFFECT_BREATHING, 1),
    "nightrider": (apiv5.EFFECT_NIGHTRIDER, 1),
}


class EngineError(RuntimeError):
    """Raised when a scheme cannot be applied."""


def _shape(rgb, st) -> tuple:
    """Set saturation from the intensity control, then scale for brightness.

    In that order: saturation works on the full-range colour, so a dim
    brightness does not starve it of headroom.

    Exactly one saturation stage. There were three - a multiplier, a
    ``min_saturation`` floor and a relative intensity trim - which overlapped
    and fought each other: the floor could lift a colour the trim had just
    taken down, and on a typical 0.67-saturated theme accent the floor never
    engaged and the trim's centre was a no-op, so the default did nothing at
    all and the keyboard read washed out. The intensity control is now an
    absolute target and the only authority on how vivid a colour is.

    This is the single funnel every colour passes through - the two gradient
    anchors, the chassis samples, solid fills and effect colours - which is why
    one control here covers every mode. Note *anchors*, not interpolated keys:
    see docs/dead-ends.md for what happened when this ran per key.
    """
    shaped = colors.apply_intensity(rgb, int(st.get("intensity", 0) or 0))
    return colors.scale(shaped, int(st.get("brightness", 255)))


def effective_effect(st) -> str:
    """ThemeSync always means the theme gradient, whatever else is stored."""
    if st.get("themesync"):
        return "gradient"
    effect = str(st.get("effect", "gradient")).lower()
    if effect not in EFFECTS:
        raise EngineError(f"unknown effect {effect!r} (expected one of {', '.join(EFFECTS)})")
    return effect


def zone_color(st, zone: str) -> tuple:
    """The colour a single zone should show, before shaping."""
    zones = st.get("zones") or {}
    if st.get("zonesync"):
        # Synced: every zone follows the keyboard's pick, so there is one
        # colour to change and no way for zones to drift apart.
        source = zones.get("kbd") or {}
    else:
        source = zones.get(zone) or {}
    spec = source.get("color") or "ffffff"
    return colors.parse_color(spec)


def secondary_color(st):
    """The explicitly chosen far end of a manual gradient, or None."""
    spec = (st.get("secondary") or "").strip()
    if not spec:
        return None
    try:
        return colors.parse_color(spec)
    except colors.ColorError:
        return None


def resolve_anchors(st) -> tuple:
    """The two ends of the gradient.

    In ThemeSync mode both come from the active theme. In manual mode the first
    picker is the near end; the second picker, when set, is the far end. With no
    second colour chosen the complement stands in, so "Gradient" is still a
    blend rather than a flat fill.
    """
    if st.get("themesync"):
        return palette.anchors()
    primary = zone_color(st, "kbd")
    chosen = secondary_color(st)
    return primary, (chosen if chosen is not None else colors.complement(primary))


def two_colour_effect(st) -> bool:
    """Whether an animated effect should animate between two chosen colours.

    Only when the user actually set a second colour and it differs from the
    first. A derived complement does not count: asking for Solid-red Pulse
    should pulse red, not pulse red-to-cyan because a complement exists.
    """
    chosen = secondary_color(st)
    if chosen is None:
        return False
    return tuple(chosen) != tuple(zone_color(st, "kbd"))


def plan(st, zones=None) -> dict:
    """Compute what to send, without touching hardware.

    Split out from ``apply`` so it can be unit-tested and so ``--dry-run`` can
    show the exact colours that would be written.
    """
    addressable = device.zone_names()
    targets = [z for z in (zones or addressable) if z in addressable]
    if not targets:
        raise EngineError("no valid zones requested")

    effect = effective_effect(st)
    result = {"effect": effect, "zones": targets, "kbd_leds": None,
              "kbd_effect": None, "elc": {}, "kbd_solid": None,
              "power_programmed": None}

    # Which chassis zones exist, and at which protocol ids, is a property of
    # the model - some have fewer, some address them differently - so it comes
    # from the keymap rather than a constant. A zone this machine does not have
    # is skipped instead of writing to an id that drives nothing.
    zone_ids = keymap.zones()
    elc_targets = [z for z in targets if z in zone_ids]

    if effect == "off":
        result["kbd_solid"] = (0, 0, 0) if "kbd" in targets else None
        result["elc"] = {zone: (0, 0, 0) for zone in elc_targets}
        return result

    if effect == "gradient":
        first, second = resolve_anchors(st)
        axis = st.get("axis", gradient.DEFAULT_AXIS)

        # Shape the two anchors, then interpolate between them.
        #
        # Never the other way round. _shape sets saturation to an absolute
        # target, which is a non-linear, clamping step, and applying it to each
        # interpolated key
        # destroys the blend: a two-colour gradient is smooth precisely because
        # its saturation ramps down through the middle and back up, and clamping
        # every key flattens that ramp until only hue varies - which snaps from
        # one anchor to the other and renders as two solid blocks with a seam.
        # A lerp between two fixed endpoints cannot band; it is linear by
        # construction however non-linear the shaping that produced them.
        near, far = _shape(first, st), _shape(second, st)

        keymap_data = keymap.load()
        leds = gradient.render_kbd(keymap_data, near, far, axis)
        if "kbd" in targets:
            result["kbd_leds"] = leds

        samples = gradient.elc_samples(near, far, list(zone_ids))

        # The chassis zones sit at the corners of the blend, and take the
        # *exact* colour of the corner key rather than a fixed point on the
        # axis - t=1.0 lands slightly past the bottom-right key, which is at
        # 0.969 on this keymap. Zones this machine does not have are left as
        # elc_samples spread them.
        try:
            near_index, far_index = gradient.extreme_indices(keymap_data, axis)
            by_index = {i: (r, g, b) for i, r, g, b in leds}
            corner_near = by_index.get(near_index, near)
            corner_far = by_index.get(far_index, far)
            for zone, colour in (("tpd", corner_near),    # touchpad ring == esc
                                 ("logo", corner_near),   # lid, the near end
                                 ("pbtn", corner_far)):   # power == bottom-right key
                if zone in samples:
                    samples[zone] = colour
        except gradient.GradientError:
            pass

        result["elc"] = {zone: samples[zone] for zone in elc_targets}
        return result

    if effect == "solid":
        if "kbd" in targets:
            result["kbd_solid"] = _shape(zone_color(st, "kbd"), st)
        result["elc"] = {zone: _shape(zone_color(st, zone), st) for zone in elc_targets}
        return result

    # Animated: the keyboard runs it in firmware.
    code, _ = _FIRMWARE_EFFECTS[effect]
    first, second = resolve_anchors(st)
    two_colour = two_colour_effect(st)

    palette_colours = [_shape(first, st)]
    if two_colour:
        palette_colours.append(_shape(second, st))

    if "kbd" in targets:
        tempo = apiv5.SPEED_PRESETS.get(str(st.get("speed", "medium")).lower(), 60)
        result["kbd_effect"] = {"code": code, "colours": palette_colours, "tempo": tempo}

    if two_colour:
        # With a range chosen, the chassis samples it the way the gradient does,
        # so the whole machine reads as one blend rather than the keyboard
        # animating a range while three lights sit on one end of it.
        samples = gradient.elc_samples(first, second, list(zone_ids))
        result["elc"] = {zone: _shape(samples[zone], st) for zone in elc_targets}
    else:
        result["elc"] = {zone: _shape(zone_color(st, zone), st) for zone in elc_targets}
    return result


def apply(st, zones=None, persist: bool = False, fast: bool = False) -> dict:
    """Apply a state to the hardware. Returns the plan that was written.

    ``fast`` skips the slow power-button state programming, for the
    intermediate frames of a live colour drag.
    """
    work = plan(st, zones)
    needed = list(work["zones"])
    fds = device.open_fds(needed)
    errors = []

    # The two controllers are independent devices on independent descriptors,
    # and they are wildly different speeds: the chassis ioctl blocks ~63ms per
    # packet while the keyboard's returns in ~2ms. Writing them in sequence
    # makes every keyboard repaint wait behind the slow chassis for no reason,
    # so each controller gets its own thread and the apply costs the slower of
    # the two rather than their sum.
    def drive_chassis():
        elc_fd = fds.get("elc")
        if elc_fd is None or not work["elc"]:
            return
        # Group zones sharing a colour so identical colours cost one packet
        # instead of three - worth real time at 63ms each.
        # Ids come from the keymap, not from the reference map: a model with a
        # zone this build has never heard of must still be addressable, and
        # indexing device.ELC_ZONES here would raise KeyError on it.
        zone_ids = keymap.zones()
        grouped = {}
        for zone, rgb in work["elc"].items():
            ids = zone_ids.get(zone)
            if not ids:
                continue
            grouped.setdefault(tuple(rgb), []).extend(ids)
        apiv4.begin(elc_fd)
        for rgb, ids in grouped.items():
            apiv4.set_one_color(elc_fd, rgb, ids)
        apiv4.commit(elc_fd)
        if persist:
            apiv4.persist(elc_fd, list(grouped.items()))

        # The power button needs more than a colour write: the firmware's own
        # power-state handler overwrites it at the next AC/battery/sleep
        # transition. Programming the six state blocks is what makes the colour
        # stick, and it costs ~2s - so anything the user is actively driving
        # passes fast=True and leaves it to a later commit.
        if not fast and "pbtn" in work["elc"]:
            # apiv4 decides whether the write is needed: it knows what this
            # process last put in NVRAM. Deliberately not read from saved state
            # - a fresh process cannot know what the firmware currently holds,
            # and assuming otherwise left the button reverting at the next
            # power transition.
            if apiv4.program_power_button(elc_fd, work["elc"]["pbtn"]):
                work["power_programmed"] = colors.to_hex(work["elc"]["pbtn"])

    def drive_keyboard():
        kbd_fd = fds.get("kbd")
        if kbd_fd is None:
            return
        # Whether the teardown is needed is apiv5's business: it knows what this
        # process last told the controller. It is deliberately not read from
        # saved state - a fresh process must not assume what an earlier one did.
        if work["kbd_leds"] is not None:
            apiv5.paint(kbd_fd, work["kbd_leds"])
        elif work["kbd_effect"] is not None:
            spec = work["kbd_effect"]
            apiv5.firmware_effect(kbd_fd, spec["code"], spec["colours"],
                                  tempo=spec["tempo"])
        elif work["kbd_solid"] is not None:
            apiv5.solid(kbd_fd, work["kbd_solid"])

    def guarded(fn):
        def run():
            try:
                fn()
            except Exception as exc:  # surfaced after both threads finish
                errors.append(exc)
        return run

    try:
        threads = [threading.Thread(target=guarded(fn), daemon=True)
                   for fn in (drive_chassis, drive_keyboard)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        if errors:
            raise errors[0]
    finally:
        device.close_fds(fds)
    return work
