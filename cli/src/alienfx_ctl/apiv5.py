"""APIv5 - the per-key keyboard controller.

Wire format is a 64-byte feature report: report id ``0xCC`` followed by up to
63 payload bytes, written with ``HIDIOCSFEATURE``.

A colour change is a *sequence*, not a packet: clean-switch, turn-on-set,
one or more colour frames, loop, update.  Sending a partial sequence - in
particular skipping turn-on-set - leaves the controller in a dark state that
only a reboot clears, so every public function here emits a complete sequence.
"""

from __future__ import annotations

import fcntl
import time

from .hid import hidiocsfeature

KBD_REPORT_ID = 0xCC
KBD_REPORT_LEN = 64
KBD_PAYLOAD_MAX = KBD_REPORT_LEN - 1

#: LED index space the controller accepts. Only ~85 are physically present.
KBD_LED_COUNT = 200

#: A colour frame carries a 3-byte header plus 4 bytes per LED, and the payload
#: is 63 bytes, so 15 LEDs is the most that fits in one packet.
_LEDS_PER_PACKET = 15

# Pacing. Unlike the chassis, this controller is fast: its ioctl returns in
# ~1.9ms. The original delays here were an order of magnitude larger than the
# work, which made a repaint ~90% sleep, so they are tuned down and named.
#
# RESET_SETTLE stays generous: the controller genuinely needs a moment after a
# reset, and getting that wrong is how you end up with a dark keyboard that
# only a reboot clears.
#
# FRAME_SETTLE is per colour packet, and a full-range paint is 14 of them, so it
# is the one that compounds. The ioctl itself already blocks ~2.6ms, so 1ms here
# still leaves a real gap between packets. The command *sequence* is deliberately
# not shortened any further than the conditional clean_switch: a partial APIv5
# sequence leaves the controller dark until a reboot, and that is not a trade
# worth a few milliseconds.
_RESET_SETTLE = 0.05
_STEP_SETTLE = 0.01
_FRAME_SETTLE = 0.001

_CMD_EFFECT = 0x80
_CMD_TURN_ON_SET = 0x83
_CMD_UPDATE = 0x8B
_CMD_FRAME = 0x8C
_CMD_STATUS = 0x93
_CMD_RESET = 0x94

#: Firmware effect codes verified on m16 R2 / BIOS 1.19.0.
#: BREATHING is the only code that actually fades smoothly, so it is what
#: drives the user-facing "Pulse" effect.  PULSE (8), DUAL_WAVE (4) and
#: LASER (11) are deliberately absent: they either hard-strobe or freeze.
EFFECT_BREATHING = 2
EFFECT_WAVE = 3
EFFECT_NIGHTRIDER = 10

#: Firmware tempo is inverted - a lower number is a slower animation.
SPEED_PRESETS = {
    "slowest": 20,
    "slow": 40,
    "medium": 60,
    "fast": 90,
    "fastest": 120,
}


def kbd_send(fd: int, payload) -> None:
    """Send one 64-byte feature report, zero-padded, with the report id."""
    data = bytes(payload)
    if len(data) > KBD_PAYLOAD_MAX:
        raise ValueError(f"keyboard payload too long: {len(data)} > {KBD_PAYLOAD_MAX}")
    buf = bytearray(KBD_REPORT_LEN)
    buf[0] = KBD_REPORT_ID
    buf[1 : 1 + len(data)] = data
    fcntl.ioctl(fd, hidiocsfeature(KBD_REPORT_LEN), bytes(buf))


def reset(fd: int) -> None:
    kbd_send(fd, [_CMD_RESET])
    time.sleep(_RESET_SETTLE)


def update(fd: int) -> None:
    kbd_send(fd, [_CMD_UPDATE, 0x01, 0xFF])
    time.sleep(_STEP_SETTLE)


def clean_switch(fd: int) -> None:
    """Tear down whatever is currently running before loading a static frame.

    Only used ahead of painted frames.  Firmware effects must *not* be preceded
    by this - the disable frame strobes on the way past.
    """
    kbd_send(fd, [_CMD_EFFECT, 0x01, 0xFE, 0x00, 0x00, 0x01, 0x01, 0x01])
    update(fd)
    reset(fd)


def _turn_on_set(fd: int, brightness: int = 0xFF) -> None:
    kbd_send(fd, [_CMD_TURN_ON_SET, 0x38, 0x9C, max(0, min(255, int(brightness)))])
    time.sleep(_STEP_SETTLE)


def _frames(fd: int, leds) -> None:
    """Send colour frames. ``leds`` is an iterable of (index, r, g, b).

    The LED index goes on the wire one higher than its real value.
    """
    batch = []
    for index, red, green, blue in leds:
        batch.append((index, red, green, blue))
        if len(batch) == _LEDS_PER_PACKET:
            _frame(fd, batch)
            batch = []
    if batch:
        _frame(fd, batch)


def _frame(fd: int, batch) -> None:
    payload = [_CMD_FRAME, 0x02, 0x00]
    for index, red, green, blue in batch:
        payload += [
            max(0, min(255, int(index) + 1)),
            max(0, min(255, int(red))),
            max(0, min(255, int(green))),
            max(0, min(255, int(blue))),
        ]
    kbd_send(fd, payload)
    time.sleep(_FRAME_SETTLE)


def _commit(fd: int) -> None:
    kbd_send(fd, [_CMD_FRAME, 0x13])
    time.sleep(_STEP_SETTLE)


def paint(fd: int, leds, brightness: int = 0xFF, clean: bool = True) -> None:
    """Paint an explicit set of LEDs as one complete, committed frame.

    ``clean`` runs the teardown that stops a firmware effect before painting.
    It is required when coming from an effect, and pure overhead when the
    controller is already showing a painted frame - it costs ~84ms, which is a
    third of a repaint, so a colour drag skips it.
    """
    if clean:
        clean_switch(fd)
    _turn_on_set(fd, brightness)
    _frames(fd, leds)
    _commit(fd)
    update(fd)


def solid(fd: int, rgb, count: int = KBD_LED_COUNT, clean: bool = True) -> None:
    red, green, blue = (max(0, min(255, int(channel))) for channel in rgb)
    paint(fd, [(i, red, green, blue) for i in range(count)], clean=clean)


def off(fd: int, count: int = KBD_LED_COUNT) -> None:
    """Black out every key.

    The firmware has no LED power-down, so "off" means painting black.  Note
    the earlier implementation painted *white* here, which is why turning the
    keyboard off used to light it up.
    """
    paint(fd, [(i, 0, 0, 0) for i in range(count)])


def firmware_effect(fd: int, effect: int, rgb, tempo: int = 60, colours: int = 1) -> None:
    """Hand an animation to the controller so it runs without the host.

    Deliberately does not clean-switch first; that would strobe.
    """
    red, green, blue = (max(0, min(255, int(channel))) for channel in rgb)
    reset(fd)
    kbd_send(
        fd,
        [
            _CMD_EFFECT, int(effect), max(1, min(255, int(tempo))),
            0x00, 0x00, 0x01, 0x01, 0x01,
            max(0, int(colours) - 1),
            red, green, blue,
            0x00, 0x00, 0x00,
        ],
    )
    time.sleep(_STEP_SETTLE)
    update(fd)
