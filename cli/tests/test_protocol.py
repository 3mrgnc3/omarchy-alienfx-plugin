"""Wire-format tests for the two HID protocols.

The byte layouts here were reverse-engineered and confirmed against real
hardware. They are pinned deliberately: an accidental edit produces lighting
that silently does nothing, or - for the keyboard - a controller stuck dark
until reboot.
"""

import pytest

from alienfx_ctl import apiv4, apiv5, hid


def test_ioctl_numbers():
    # (dir<<30) | (size<<16) | ('H'<<8) | nr
    assert hid.hidiocsfeature(64) == (3 << 30) | (64 << 16) | (ord("H") << 8) | 0x06
    assert hid.hidiocsoutput(33) == (3 << 30) | (33 << 16) | (ord("H") << 8) | 0x0B
    assert hid.hidiocgfeature(64) == (3 << 30) | (64 << 16) | (ord("H") << 8) | 0x07


class Recorder:
    """Captures the exact buffers that would reach the ioctl."""

    def __init__(self):
        self.frames = []

    def install(self, monkeypatch, module):
        monkeypatch.setattr(module.fcntl, "ioctl",
                            lambda fd, req, buf: self.frames.append((req, bytes(buf))))
        monkeypatch.setattr(module.time, "sleep", lambda _s: None)


# ------------------------------------------------------------------- APIv4

def test_elc_reports_are_exactly_33_bytes(monkeypatch):
    rec = Recorder(); rec.install(monkeypatch, apiv4)
    apiv4.elc_send(3, [0x03, 0x27, 1, 2, 3])
    req, buf = rec.frames[0]
    assert len(buf) == apiv4.ELC_REPORT_LEN == 33
    assert buf[:5] == bytes([0x03, 0x27, 1, 2, 3])
    assert buf[5:] == bytes(28), "the tail must be zero-padded"
    assert req == hid.hidiocsoutput(33)


def test_elc_rejects_an_oversized_payload():
    with pytest.raises(ValueError):
        apiv4.elc_send(3, [0] * 34)


def test_elc_set_one_color_layout(monkeypatch):
    rec = Recorder(); rec.install(monkeypatch, apiv4)
    apiv4.set_one_color(3, (0x11, 0x22, 0x33), [0x00, 0x02])
    _req, buf = rec.frames[0]
    # [0x03, 0x27, R, G, B, 0x00, count, id...]
    assert buf[:9] == bytes([0x03, 0x27, 0x11, 0x22, 0x33, 0x00, 0x02, 0x00, 0x02])


def test_elc_solid_is_wrapped_in_a_control_block(monkeypatch):
    """Without the start/play wrapper the firmware discards the colour."""
    rec = Recorder(); rec.install(monkeypatch, apiv4)
    apiv4.solid(3, (255, 0, 0), [0x00])
    payloads = [buf[:6] for _req, buf in rec.frames]
    assert payloads[0] == bytes([0x03, 0x21, 0x00, 0x01, 0x00, 0xFF])   # start
    assert payloads[1][:2] == bytes([0x03, 0x27])                        # colour
    assert payloads[2] == bytes([0x03, 0x21, 0x00, 0x03, 0x00, 0xFF])   # play


def test_elc_off_sends_black(monkeypatch):
    rec = Recorder(); rec.install(monkeypatch, apiv4)
    apiv4.off(3, [0x00])
    colour = [buf for _r, buf in rec.frames if buf[1] == 0x27][0]
    assert colour[2:5] == bytes([0, 0, 0])


def test_elc_solid_with_no_zones_sends_nothing(monkeypatch):
    rec = Recorder(); rec.install(monkeypatch, apiv4)
    apiv4.solid(3, (1, 2, 3), [])
    assert rec.frames == []


def test_persist_uses_one_save_wrapper_for_all_zones(monkeypatch):
    """Wrapping each zone separately makes every save discard the previous one,
    so only the last zone survives. Pin the single-wrapper shape."""
    rec = Recorder(); rec.install(monkeypatch, apiv4)
    apiv4.persist(3, [((1, 0, 0), [0x00]), ((0, 1, 0), [0x02])])

    control = [buf[3] for _r, buf in rec.frames if buf[1] == apiv4._OP_CONTROL]
    colours = [buf for _r, buf in rec.frames if buf[1] == 0x27]
    assert len(colours) == 2, "both zones must be programmed"
    assert control.count(apiv4._SUB_SAVE) == 1, "exactly one save for the batch"
    assert control == [apiv4._SUB_REMOVE, apiv4._SUB_START,
                       apiv4._SUB_SAVE, apiv4._SUB_SET_DEFAULT]
    # Every control packet must address the persisted NVRAM block, not the
    # volatile one, or nothing survives a cold boot.
    assert all(buf[5] == apiv4.CID_DEFAULT
               for _r, buf in rec.frames if buf[1] == apiv4._OP_CONTROL)


# ------------------------------------------------------------------- APIv5

def test_kbd_reports_are_64_bytes_with_the_report_id(monkeypatch):
    rec = Recorder(); rec.install(monkeypatch, apiv5)
    apiv5.kbd_send(4, [0x94])
    req, buf = rec.frames[0]
    assert len(buf) == apiv5.KBD_REPORT_LEN == 64
    assert buf[0] == apiv5.KBD_REPORT_ID == 0xCC
    assert buf[1] == 0x94
    assert buf[2:] == bytes(62)
    assert req == hid.hidiocsfeature(64)


def test_kbd_rejects_an_oversized_payload():
    with pytest.raises(ValueError):
        apiv5.kbd_send(4, [0] * 64)


def test_paint_emits_the_full_sequence(monkeypatch):
    """Reset, turn-on-set, frames, loop, update. Skipping turn-on-set is what
    used to leave the controller stuck dark."""
    rec = Recorder(); rec.install(monkeypatch, apiv5)
    apiv5.paint(4, [(0, 1, 2, 3)])
    heads = [buf[1] for _r, buf in rec.frames]
    assert 0x94 in heads, "no reset"
    assert 0x83 in heads, "no turn-on-set"
    assert 0x8C in heads, "no colour frame"
    assert heads[-1] == 0x8B, "sequence must end with update"


def test_led_index_goes_on_the_wire_one_higher(monkeypatch):
    rec = Recorder(); rec.install(monkeypatch, apiv5)
    apiv5.paint(4, [(0, 0x11, 0x22, 0x33)])
    frame = [buf for _r, buf in rec.frames if buf[1] == 0x8C and buf[2] == 0x02][0]
    assert frame[1:4] == bytes([0x8C, 0x02, 0x00])
    assert frame[4] == 1, "LED 0 must be sent as 1"
    assert frame[5:8] == bytes([0x11, 0x22, 0x33])


def test_frames_are_batched_at_fifteen_leds(monkeypatch):
    """3 header bytes + 4 per LED must fit the 63-byte payload."""
    rec = Recorder(); rec.install(monkeypatch, apiv5)
    apiv5.paint(4, [(i, 1, 2, 3) for i in range(31)])
    frames = [buf for _r, buf in rec.frames if buf[1] == 0x8C and buf[2] == 0x02]
    assert len(frames) == 3  # 15 + 15 + 1
    assert frames[0][4:].rstrip(b"\x00") != b""
    assert len(frames[0]) == 64


def test_off_paints_black_not_white(monkeypatch):
    """The previous implementation painted white here, so turning the keyboard
    off lit it up instead."""
    rec = Recorder(); rec.install(monkeypatch, apiv5)
    apiv5.off(4, count=2)
    frame = [buf for _r, buf in rec.frames if buf[1] == 0x8C and buf[2] == 0x02][0]
    assert frame[5:8] == bytes([0, 0, 0])
    assert frame[9:12] == bytes([0, 0, 0])


def test_firmware_effect_does_not_clean_switch_first(monkeypatch):
    """clean_switch strobes on the way past, so effects must skip it."""
    rec = Recorder(); rec.install(monkeypatch, apiv5)
    apiv5.firmware_effect(4, apiv5.EFFECT_WAVE, (255, 0, 0), tempo=40)
    heads = [buf[1] for _r, buf in rec.frames]
    # A clean switch would begin with the 0x80/0x01/0xFE disable frame.
    disables = [buf for _r, buf in rec.frames if buf[1] == 0x80 and buf[2] == 0x01 and buf[3] == 0xFE]
    assert disables == []
    assert heads[0] == 0x94
    assert 0x80 in heads


def test_firmware_effect_payload(monkeypatch):
    rec = Recorder(); rec.install(monkeypatch, apiv5)
    apiv5.firmware_effect(4, apiv5.EFFECT_NIGHTRIDER, (0x0A, 0x0B, 0x0C), tempo=55, colours=1)
    effect = [buf for _r, buf in rec.frames if buf[1] == 0x80][0]
    assert effect[2] == apiv5.EFFECT_NIGHTRIDER == 10
    assert effect[3] == 55
    assert effect[9] == 0, "colours-1 must be encoded"
    assert effect[10:13] == bytes([0x0A, 0x0B, 0x0C])


def test_only_verified_effect_codes_are_exposed():
    """PULSE(8), DUAL_WAVE(4) and LASER(11) do not render usefully on the
    reference BIOS, so they must not be offered."""
    assert apiv5.EFFECT_BREATHING == 2
    assert apiv5.EFFECT_WAVE == 3
    assert apiv5.EFFECT_NIGHTRIDER == 10
    codes = {v for k, v in vars(apiv5).items() if k.startswith("EFFECT_")}
    assert codes == {2, 3, 10}


def test_speed_presets_are_inverted():
    """Firmware tempo counts up as it speeds up, which is the opposite of what
    the preset names suggest."""
    presets = apiv5.SPEED_PRESETS
    assert presets["slowest"] < presets["slow"] < presets["medium"] < presets["fast"] < presets["fastest"]


def test_channels_are_clamped(monkeypatch):
    rec = Recorder(); rec.install(monkeypatch, apiv5)
    apiv5.paint(4, [(0, 999, -5, 128)])
    frame = [buf for _r, buf in rec.frames if buf[1] == 0x8C and buf[2] == 0x02][0]
    assert frame[5:8] == bytes([255, 0, 128])
