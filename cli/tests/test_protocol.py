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


# ------------------------------------------------------- APIv4 power states

def test_public_control_helpers_match_the_raw_packets(monkeypatch):
    rec = Recorder(); rec.install(monkeypatch, apiv4)
    apiv4.begin(9)
    apiv4.commit(9)
    payloads = [buf[:6] for _r, buf in rec.frames]
    assert payloads[0] == bytes([0x03, 0x21, 0x00, 0x01, 0x00, 0xFF])
    assert payloads[1] == bytes([0x03, 0x21, 0x00, 0x03, 0x00, 0xFF])


def test_color_sel_layout(monkeypatch):
    rec = Recorder(); rec.install(monkeypatch, apiv4)
    apiv4.color_sel(9, [0x04], loop=1)
    _req, buf = rec.frames[0]
    assert buf[:6] == bytes([0x03, 0x23, 0x01, 0x00, 0x01, 0x04])


def test_color_set_static_uses_the_marker_tempo(monkeypatch):
    """A static colour carries 0xFA in the tempo byte; it is a marker, not a
    speed, and using the caller's tempo there makes the colour not apply."""
    rec = Recorder(); rec.install(monkeypatch, apiv4)
    apiv4.color_set(9, [(apiv4.ACT_COLOR, 1, 2, 3)], time=0x64, tempo=0x64)
    _req, buf = rec.frames[0]
    assert buf[1] == 0x24
    assert buf[2] == apiv4.ACT_COLOR
    assert buf[4] == 0xD0, "static colour opcode"
    assert buf[6] == 0xFA, "static tempo marker"
    assert buf[7:10] == bytes([1, 2, 3])


def test_color_set_effect_uses_the_caller_tempo(monkeypatch):
    rec = Recorder(); rec.install(monkeypatch, apiv4)
    apiv4.color_set(9, [(apiv4.ACT_PULSE, 255, 0, 0)], time=0x80, tempo=0x40)
    _req, buf = rec.frames[0]
    assert buf[2] == apiv4.ACT_PULSE
    assert buf[3] == 0x80
    assert buf[4] == 0xDC, "pulse opcode"
    assert buf[6] == 0x40, "effect tempo must be the caller's"


def test_act_power_is_not_clamped_to_morph(monkeypatch):
    """The firmware clamps unknown action types to morph, but ACT_POWER is a
    real distinct opcode. The first archived implementation clamped it, which
    programmed the sleep fade as a plain morph."""
    rec = Recorder(); rec.install(monkeypatch, apiv4)
    apiv4.color_set(9, [(apiv4.ACT_POWER, 10, 20, 30), (apiv4.ACT_POWER, 0, 0, 0)])
    _req, buf = rec.frames[0]
    assert buf[2] == apiv4.ACT_POWER == 6, "type must stay 6, not become 2"
    assert buf[4] == 0xE8, "power-morph opcode"


def test_color_set_carries_up_to_three_triplets(monkeypatch):
    rec = Recorder(); rec.install(monkeypatch, apiv4)
    apiv4.color_set(9, [(apiv4.ACT_MORPH, 1, 1, 1), (apiv4.ACT_MORPH, 2, 2, 2),
                        (apiv4.ACT_MORPH, 3, 3, 3), (apiv4.ACT_MORPH, 4, 4, 4)])
    _req, buf = rec.frames[0]
    assert buf[7:16] == bytes([1, 1, 1, 2, 2, 2, 3, 3, 3])
    assert buf[16] == 0, "the fourth action must be dropped, not appended"


def test_color_set_with_no_actions_sends_nothing(monkeypatch):
    rec = Recorder(); rec.install(monkeypatch, apiv4)
    apiv4.color_set(9, [])
    assert rec.frames == []


def test_set_power_state_sequence(monkeypatch):
    """remove -> start -> select -> set -> save, all against the same CID."""
    rec = Recorder(); rec.install(monkeypatch, apiv4)
    apiv4.set_power_state(9, apiv4.CID_POWER_AC, 0x04, [(apiv4.ACT_COLOR, 5, 6, 7)])
    ops = [(buf[1], buf[3] if buf[1] == 0x22 else None) for _r, buf in rec.frames]
    assert ops[0] == (0x22, apiv4._SUB_REMOVE)
    assert ops[1] == (0x22, apiv4._SUB_START)
    assert ops[2][0] == 0x23
    assert ops[3][0] == 0x24
    assert ops[4] == (0x22, apiv4._SUB_SAVE)
    cids = [buf[5] for _r, buf in rec.frames if buf[1] == 0x22]
    assert set(cids) == {apiv4.CID_POWER_AC}


def test_program_power_button_covers_all_six_states(monkeypatch):
    rec = Recorder(); rec.install(monkeypatch, apiv4)
    apiv4.program_power_button(9, (200, 100, 50))

    cids = {buf[5] for _r, buf in rec.frames if buf[1] == 0x22}
    assert cids == {
        apiv4.CID_POWER_AC_SLEEP, apiv4.CID_POWER_AC, apiv4.CID_POWER_AC_CHARGING,
        apiv4.CID_POWER_BAT_SLEEP, apiv4.CID_POWER_BAT, apiv4.CID_POWER_BAT_CRITICAL,
    }
    # Each state must be saved exactly once.
    saves = [buf[5] for _r, buf in rec.frames if buf[1] == 0x22 and buf[3] == apiv4._SUB_SAVE]
    assert sorted(saves) == sorted(cids)
    # And the block has to be played, or nothing takes effect.
    assert any(buf[1] == 0x21 and buf[3] == apiv4._SUB_PLAY_ONLY for _r, buf in rec.frames)


def test_program_power_button_targets_only_the_power_button(monkeypatch):
    rec = Recorder(); rec.install(monkeypatch, apiv4)
    apiv4.program_power_button(9, (10, 20, 30))
    selected = {tuple(buf[5:5 + buf[4]]) for _r, buf in rec.frames if buf[1] == 0x23}
    assert selected == {(0x04,)}


def test_power_button_shows_the_chosen_colour_on_ac_and_battery(monkeypatch):
    """The two states the user actually looks at must be the exact colour."""
    rec = Recorder(); rec.install(monkeypatch, apiv4)
    apiv4.program_power_button(9, (200, 100, 50))

    # Walk the stream keeping track of which CID we are inside.
    current, seen = None, {}
    for _r, buf in rec.frames:
        if buf[1] == 0x22 and buf[3] == apiv4._SUB_START:
            current = buf[5]
        elif buf[1] == 0x24 and current is not None:
            seen.setdefault(current, (buf[2], bytes(buf[7:10])))

    assert seen[apiv4.CID_POWER_AC] == (apiv4.ACT_COLOR, bytes([200, 100, 50]))
    assert seen[apiv4.CID_POWER_BAT] == (apiv4.ACT_COLOR, bytes([200, 100, 50]))
    # Critical battery stays a red warning regardless of the chosen colour -
    # the one piece of firmware behaviour worth keeping.
    assert seen[apiv4.CID_POWER_BAT_CRITICAL] == (apiv4.ACT_PULSE, bytes([255, 0, 0]))
    # Sleep fades the colour out.
    assert seen[apiv4.CID_POWER_AC_SLEEP][0] == apiv4.ACT_POWER


def test_power_button_charging_breathes_between_dim_and_full(monkeypatch):
    rec = Recorder(); rec.install(monkeypatch, apiv4)
    apiv4.program_power_button(9, (255, 200, 100))
    current = None
    charging = None
    for _r, buf in rec.frames:
        if buf[1] == 0x22 and buf[3] == apiv4._SUB_START:
            current = buf[5]
        elif buf[1] == 0x24 and current == apiv4.CID_POWER_AC_CHARGING:
            charging = buf
            break
    assert charging is not None
    dim, full = bytes(charging[7:10]), bytes(charging[10:13])
    assert full == bytes([255, 200, 100])
    assert all(d < f for d, f in zip(dim, full) if f > 0), "dim end must be dimmer"
