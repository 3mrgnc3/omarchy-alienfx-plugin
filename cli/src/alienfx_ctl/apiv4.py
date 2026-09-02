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

#: The firmware wants a short pause between packets; without it the controller
#: drops some of a burst and you get partially-applied colour.
ELC_PACKET_DELAY = 0.02

_OP_CONTROL = 0x21
_OP_SET_ONE_COLOR = 0x27

_SUB_START = 0x01
_SUB_SAVE = 0x02
_SUB_PLAY = 0x03
_SUB_REMOVE = 0x04
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
