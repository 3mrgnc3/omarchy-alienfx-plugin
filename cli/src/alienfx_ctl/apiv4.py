"""APIv4 - the AW-ELC chassis controller (touchpad halo, lid logo, power button).

Wire format is a fixed 33-byte output report with no report-id prefix, written
with the ``HIDIOCSOUTPUT`` ioctl.  Colour changes have to be wrapped in a
control block (``start`` ... ``play``) or the firmware discards them.
"""

from __future__ import annotations

import fcntl
import time

from .hid import hidiocsoutput

ELC_REPORT_LEN = 33

#: A short pause between packets; without any, the controller drops part of a
#: burst and you get partially-applied colour.
#:
#: Measured on the reference device, the ioctl itself blocks for about 62ms per
#: packet, so it already provides the pacing on its own. Dropping this from the
#: original 20ms to 5ms changed a 34-packet run by nothing measurable, which is
#: the useful finding: chassis writes are slow because the controller is slow,
#: not because of anything the host does. Programming the six power-state
#: blocks costs ~2.3s and no amount of tuning here will change that.
ELC_PACKET_DELAY = 0.005

_OP_CONTROL = 0x21
_OP_SET_ONE_COLOR = 0x27

_SUB_START = 0x01
_SUB_SAVE = 0x02
_SUB_PLAY = 0x03
_SUB_REMOVE = 0x04
_SUB_PLAY_ONLY = 0x05
_SUB_SET_DEFAULT = 0x06

#: Volatile "common" block - applies now, forgotten on power loss.
CID_VOLATILE = 0xFF
#: Persisted "light defaults" block - survives a cold boot in NVRAM.
CID_DEFAULT = 0x61


def elc_send(fd: int, payload) -> None:
    """Send one 33-byte output report, zero-padded."""
    data = bytes(payload)
    if len(data) > ELC_REPORT_LEN:
        raise ValueError(f"ELC payload too long: {len(data)} > {ELC_REPORT_LEN}")
    buf = bytearray(ELC_REPORT_LEN)
    buf[: len(data)] = data
    fcntl.ioctl(fd, hidiocsoutput(ELC_REPORT_LEN), bytes(buf))
    time.sleep(ELC_PACKET_DELAY)


def _control(fd: int, subcommand: int, cid: int = CID_VOLATILE) -> None:
    elc_send(fd, [0x03, _OP_CONTROL, 0x00, subcommand, 0x00, cid])


def begin(fd: int, cid: int = CID_VOLATILE) -> None:
    """Open a control block. Colour writes outside one are discarded."""
    _control(fd, _SUB_START, cid)


def commit(fd: int, cid: int = CID_VOLATILE) -> None:
    """Close a control block and play it."""
    _control(fd, _SUB_PLAY, cid)


def set_one_color(fd: int, rgb, zone_ids) -> None:
    """Set a flat colour on one or more chassis light ids.

    Light ids are 0-based indices, not a bitmask.
    """
    red, green, blue = (max(0, min(255, int(channel))) for channel in rgb)
    ids = list(zone_ids)
    elc_send(fd, [0x03, _OP_SET_ONE_COLOR, red, green, blue, 0x00, len(ids), *ids])


def solid(fd: int, rgb, zone_ids) -> None:
    """Apply a flat colour immediately."""
    ids = list(zone_ids)
    if not ids:
        return
    _control(fd, _SUB_START)
    set_one_color(fd, rgb, ids)
    _control(fd, _SUB_PLAY)


def off(fd: int, zone_ids) -> None:
    """Black out chassis zones."""
    solid(fd, (0, 0, 0), zone_ids)


def persist(fd: int, zone_colors) -> None:
    """Write colours into NVRAM so they survive a cold boot.

    ``zone_colors`` maps an rgb tuple to the light ids that should take it.

    All zones must be programmed inside a *single* save wrapper.  Wrapping each
    zone separately makes every save discard the previous one, so only the last
    zone survives - a bug that cost the earlier implementation a lot of time.
    """
    items = [(rgb, list(ids)) for rgb, ids in zone_colors if ids]
    if not items:
        return
    _control(fd, _SUB_REMOVE, CID_DEFAULT)
    _control(fd, _SUB_START, CID_DEFAULT)
    for rgb, ids in items:
        set_one_color(fd, rgb, ids)
    _control(fd, _SUB_SAVE, CID_DEFAULT)
    _control(fd, _SUB_SET_DEFAULT, CID_DEFAULT)


# ---------------------------------------------------------------- power states
#
# The power button is different from the other two chassis zones. The firmware
# runs its own power-state animation for it, and that handler overwrites
# whatever `set_one_color` wrote at the next AC/battery/sleep transition - which
# is why a plain colour set on this zone appears to work and then reverts.
#
# Making a colour stick means programming the six power-state blocks in NVRAM so
# the firmware's own handler shows the colour we want.

_OP_POWER = 0x22
_OP_COLOR_SEL = 0x23
_OP_COLOR_SET = 0x24

CID_POWER_AC_SLEEP = 0x5B
CID_POWER_AC = 0x5C
CID_POWER_AC_CHARGING = 0x5D
CID_POWER_BAT_SLEEP = 0x5E
CID_POWER_BAT = 0x5F
CID_POWER_BAT_CRITICAL = 0x60

#: Action types. Anything else the firmware clamps to morph - except ACT_POWER,
#: which is a distinct opcode and must survive unclamped or the sleep fade is
#: programmed as a plain morph instead.
ACT_COLOR = 0
ACT_PULSE = 1
ACT_MORPH = 2
ACT_POWER = 6

_ACT_OPCODES = {ACT_COLOR: 0xD0, ACT_PULSE: 0xDC, ACT_MORPH: 0xCF, ACT_POWER: 0xE8}

#: For a static colour the tempo byte is a marker, not a speed.
_STATIC_TEMPO_MARKER = 0xFA


def color_sel(fd: int, zone_ids, loop: int = 1) -> None:
    """Select the zones that the next ``color_set`` applies to."""
    ids = list(zone_ids)
    elc_send(fd, [0x03, _OP_COLOR_SEL, loop, 0x00, len(ids), *ids])


def color_set(fd: int, actions, time: int = 0x64, tempo: int = 0x64) -> None:
    """Set up to three colour actions on the previously selected zones.

    ``actions`` is a list of ``(type, r, g, b)``. The packet header carries the
    type and opcode of the *first* action; later actions contribute only their
    RGB triplet.
    """
    items = list(actions)[:3]
    if not items:
        return
    first_type = items[0][0]
    opcode = _ACT_OPCODES[first_type]
    wire_type = first_type if (first_type < 4 or first_type == ACT_POWER) else ACT_MORPH
    header_tempo = _STATIC_TEMPO_MARKER if first_type == ACT_COLOR else tempo

    payload = [0x03, _OP_COLOR_SET, wire_type, time, opcode, 0x00, header_tempo]
    for _type, red, green, blue in items:
        payload += [max(0, min(255, int(red))), max(0, min(255, int(green))),
                    max(0, min(255, int(blue)))]
    elc_send(fd, payload)


def set_power_state(fd: int, cid: int, zone_id: int, actions,
                    time: int = 0x64, tempo: int = 0x64) -> None:
    """Program one power-state block: remove, start, select, set, save."""
    elc_send(fd, [0x03, _OP_POWER, 0x00, _SUB_REMOVE, 0x00, cid])
    elc_send(fd, [0x03, _OP_POWER, 0x00, _SUB_START, 0x00, cid])
    color_sel(fd, [zone_id], loop=1)
    color_set(fd, actions, time=time, tempo=tempo)
    elc_send(fd, [0x03, _OP_POWER, 0x00, _SUB_SAVE, 0x00, cid])


def program_power_button(fd: int, rgb, zone_id: int = 0x04) -> None:
    """Make a colour stick on the power button across power transitions.

    Programs all six states so the alien head shows the chosen colour on AC and
    on battery, breathes it while charging, fades it out going to sleep, and
    still warns in red when the battery is critical - keeping the one piece of
    firmware behaviour actually worth having.

    Costs roughly thirty packets at 20ms each, so this is not something to do on
    every frame of a colour drag; callers pass ``fast`` to skip it.
    """
    red, green, blue = (max(0, min(255, int(channel))) for channel in rgb)
    user = (red, green, blue)
    # ~15% of the chosen colour, as the far end of the charging breath.
    dim = (red * 40 // 255, green * 40 // 255, blue * 40 // 255)
    dark = (0, 0, 0)
    warn = (255, 0, 0)

    states = {
        CID_POWER_AC_SLEEP: ([(ACT_POWER, *user), (ACT_POWER, *dark)], 0x64, 0x64),
        CID_POWER_AC: ([(ACT_COLOR, *user)], 0x64, 0x64),
        # Slow tempo so charging glows rather than blinks.
        CID_POWER_AC_CHARGING: ([(ACT_MORPH, *dim), (ACT_MORPH, *user)], 0xFF, 0x20),
        CID_POWER_BAT_SLEEP: ([(ACT_POWER, *user), (ACT_POWER, *dark)], 0x64, 0x64),
        CID_POWER_BAT: ([(ACT_COLOR, *user)], 0x64, 0x64),
        CID_POWER_BAT_CRITICAL: ([(ACT_PULSE, *warn)], 0x80, 0x40),
    }
    for cid, (actions, time, tempo) in states.items():
        set_power_state(fd, cid, zone_id, actions, time=time, tempo=tempo)

    # Activate the programmed block.
    _control(fd, _SUB_PLAY_ONLY)
