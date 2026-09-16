"""Device resolution and descriptor ownership.

These are the tests that matter most for portability: node numbering is not
stable, and the previous generation of this tool hard-coded /dev/hidraw0 and
/dev/hidraw1. On this very laptop those numbers now belong to a security key
and the chassis controller respectively.
"""

import os

import pytest

from alienfx_ctl import device


def test_zone_tables_agree():
    assert set(device.ELC_ZONES) == set(device.ELC_ZONE_NAMES)
    assert set(device.ZONES) == {"kbd"} | set(device.ELC_ZONE_NAMES)


def test_chassis_zone_ids_are_the_probed_values():
    assert device.ELC_ZONES == {"tpd": [0x00], "logo": [0x02], "pbtn": [0x04]}


def test_which_controllers_a_zone_set_needs():
    assert device.needs_kbd(["kbd"]) and not device.needs_elc(["kbd"])
    assert device.needs_elc(["logo"]) and not device.needs_kbd(["logo"])
    assert device.needs_kbd(device.ZONES) and device.needs_elc(device.ZONES)
    assert not device.needs_kbd([]) and not device.needs_elc([])


def test_unknown_zone_is_rejected_before_any_open():
    with pytest.raises(device.DeviceError):
        device.open_fds(["kbd", "nonsense"])


def test_node_sort_is_numeric_not_lexicographic():
    """hidraw10 must not sort between hidraw1 and hidraw2."""
    names = ["hidraw10", "hidraw2", "hidraw1"]
    assert sorted(names, key=device._node_sort_key) == ["hidraw1", "hidraw2", "hidraw10"]


def test_find_node_honours_an_override(monkeypatch):
    monkeypatch.setenv("ALIENFX_KBD_DEV", "/dev/hidrawX")
    assert device.find_node(device.KBD) == "/dev/hidrawX"


def test_find_node_returns_none_when_nothing_matches(monkeypatch):
    monkeypatch.setattr(device, "iter_nodes", lambda: iter([("hidraw0", 0x1234, 0x5678)]))
    monkeypatch.setattr(device, "node_matches", lambda node, controller: False)
    assert device.find_node(device.KBD) is None


def test_verify_node_refuses_a_different_vendor(monkeypatch):
    """The guard that stops lighting packets reaching an unrelated device. On
    this laptop hidraw0 was once a security key."""
    monkeypatch.setattr(device, "_read_ids", lambda node: (0x1050, 0x0407))
    with pytest.raises(device.DeviceError) as excinfo:
        device.verify_node("/dev/hidraw0", device.ELC)
    assert "refusing to write" in str(excinfo.value)


def test_verify_node_refuses_the_right_vendor_with_the_wrong_report(monkeypatch):
    """Vendor alone is not enough - Alienware ships more than one HID device,
    and this machine has two unrelated Dell nodes. The report signature is what
    makes the match specific."""
    monkeypatch.setattr(device, "_read_ids", lambda node: (device.ELC_VID, 0x9999))
    monkeypatch.setattr(device, "report_map", lambda node: {(0, "out"): 7})
    with pytest.raises(device.DeviceError) as excinfo:
        device.verify_node("/dev/hidraw0", device.ELC)
    assert "refusing to write" in str(excinfo.value)


def test_verify_node_accepts_the_right_vendor_and_report(monkeypatch):
    """Note the product id is deliberately *not* the reference machine's: that
    is the whole point of matching on report shape."""
    monkeypatch.setattr(device, "_read_ids", lambda node: (device.ELC_VID, 0x0550))
    monkeypatch.setattr(device, "report_map", lambda node: {(0, "out"): 33})
    device.verify_node("/dev/hidraw1", device.ELC)


def test_verify_node_refuses_when_identity_is_unknown(monkeypatch):
    monkeypatch.setattr(device, "_read_ids", lambda node: None)
    with pytest.raises(device.DeviceError):
        device.verify_node("/dev/hidraw9", device.ELC)


def test_open_fds_is_atomic_and_leaks_nothing(monkeypatch):
    """If the second controller fails to open, the first must be closed again -
    otherwise a retry loop leaks descriptors and a scheme half-applies."""
    opened, closed = [], []

    def fake_open(controller):
        if controller is device.KBD:
            fd = 101
            opened.append(fd)
            return fd
        raise device.DeviceError("chassis unavailable")

    monkeypatch.setattr(device, "_open_verified", fake_open)
    monkeypatch.setattr(os, "close", lambda fd: closed.append(fd))

    with pytest.raises(device.DeviceError):
        device.open_fds(["kbd", "logo"])

    assert opened == [101]
    assert closed == [101], "the already-open descriptor was not closed"


def test_open_fds_only_opens_what_is_needed(monkeypatch):
    calls = []

    def fake_open(controller):
        calls.append(controller.key)
        return len(calls)

    monkeypatch.setattr(device, "_open_verified", fake_open)
    monkeypatch.setattr(os, "close", lambda fd: None)

    fds = device.open_fds(["logo"])
    assert list(fds) == ["elc"]
    assert len(calls) == 1
    device.close_fds(fds)
    assert fds == {}


def test_close_fds_survives_a_bad_descriptor(monkeypatch):
    def boom(fd):
        raise OSError("already closed")
    monkeypatch.setattr(os, "close", boom)
    fds = {"kbd": 7}
    device.close_fds(fds)  # must not raise
    assert fds == {}


def first_fd_value(opens):
    """The value fake_open returned on its first (and only) call."""
    return 200 + 1


# ------------------------------------------------------- descriptor cache

def test_cache_reuses_one_descriptor_across_calls(monkeypatch):
    """Stream mode applies many changes through one process. Reopening the
    chassis node each time costs ~52ms, which is most of a frame."""
    opens = []

    def fake_open(controller):
        opens.append(controller.key)
        return 200 + len(opens)

    monkeypatch.setattr(device, "_open_verified", fake_open)
    monkeypatch.setattr(os, "close", lambda fd: None)
    device.enable_cache()
    try:
        first = device.open_fds(["kbd"])
        device.close_fds(first)
        second = device.open_fds(["kbd"])
        assert len(opens) == 1, "the descriptor was reopened"
        assert second["kbd"] == first_fd_value(opens)
    finally:
        device.close_cache()


def test_close_fds_is_a_noop_while_cached(monkeypatch):
    """Closing a cached descriptor would break every later call."""
    closed = []
    monkeypatch.setattr(device, "_open_verified", lambda controller: 300)
    monkeypatch.setattr(os, "close", lambda fd: closed.append(fd))
    device.enable_cache()
    try:
        fds = device.open_fds(["kbd"])
        device.close_fds(fds)
        assert closed == [], "close_fds must not close cached descriptors"
        assert fds == {}, "the caller's dict is still cleared"
    finally:
        device.close_cache()
    assert 300 in closed, "close_cache must release it"


def test_cache_opens_only_the_controllers_asked_for(monkeypatch):
    labels = []
    monkeypatch.setattr(device, "_open_verified",
                        lambda controller: (labels.append(controller.key),
                                            400 + len(labels))[1])
    monkeypatch.setattr(os, "close", lambda fd: None)
    device.enable_cache()
    try:
        assert list(device.open_fds(["kbd"])) == ["kbd"]
        assert len(labels) == 1
        # Asking for a chassis zone now opens the second controller, and only it.
        assert sorted(device.open_fds(["kbd", "logo"])) == ["elc", "kbd"]
        assert len(labels) == 2
    finally:
        device.close_cache()


def test_cache_disabled_by_default_and_restored_after_close():
    assert device.cache_enabled() is False
    device.enable_cache()
    assert device.cache_enabled() is True
    device.close_cache()
    assert device.cache_enabled() is False


# ------------------------------------------------- zones come from the keymap
#
# Which chassis zones a machine has, and at which protocol ids, is a property of
# the model. It used to be a hard-coded four-tuple, which meant a model with
# Tron strips or four keyboard zones could not be addressed even with a correct
# keymap - open_fds rejected the names outright.

FICTIONAL = {
    "tpd": [0x00], "logo": [0x02], "pbtn": [0x04],
    "tron_left": [0x06], "tron_right": [0x07],
}


@pytest.fixture()
def other_model(monkeypatch):
    """A machine whose chassis zones this build has never heard of."""
    from alienfx_ctl import keymap
    monkeypatch.setattr(keymap, "zones", lambda data=None: dict(FICTIONAL))
    return FICTIONAL


def test_addressable_zones_follow_the_keymap(other_model):
    names = device.zone_names()
    assert names[0] == device.KBD_ZONE, "the keyboard is always first"
    assert set(names) == {"kbd"} | set(other_model)


def test_an_unfamiliar_zone_still_routes_to_the_chassis_controller(other_model):
    """needs_elc asks "is it not the keyboard?" rather than checking a list, so
    it stays right for zone names added later."""
    assert device.needs_elc(["tron_left"]) is True
    assert device.needs_elc(["kbd"]) is False
    assert device.needs_kbd(["tron_left"]) is False


def test_a_zone_this_machine_lacks_is_rejected_with_a_useful_message(other_model):
    with pytest.raises(device.DeviceError) as caught:
        device.open_fds(["nosuchzone"])
    message = str(caught.value)
    assert "nosuchzone" in message
    assert "tron_left" in message, "says what this machine actually has"


def test_the_reference_map_is_only_a_fallback():
    """With no keymap-declared zones, the reference machine's map stands in."""
    from alienfx_ctl import keymap
    assert keymap.zones({}) == dict(device.ELC_ZONES)


def test_a_six_zone_model_gets_a_theme_gradient_with_no_code_change(other_model):
    """The point of making zones dynamic. gradient.elc_samples spreads
    unfamiliar zone names evenly along the blend axis, so a model that ships a
    keymap listing five chassis zones is driven correctly by data alone."""
    from alienfx_ctl import engine, state
    st = dict(state.DEFAULT_STATE)
    st.update(brightness=255, intensity=0, effect="gradient")
    plan = engine.plan(st, list(device.zone_names()))
    assert set(plan["elc"]) == set(other_model), "every chassis zone got a colour"
    assert len(set(plan["elc"].values())) > 1, "and they are not all the same"


def test_the_chassis_writer_uses_keymap_ids_not_the_reference_map(other_model, monkeypatch):
    """It indexed device.ELC_ZONES directly, which raises KeyError on a zone
    name the reference machine does not have - so a model with Tron strips would
    have crashed on apply rather than lighting them."""
    from alienfx_ctl import apiv4, device as dev, engine, state
    written = []
    # Silence the wire entirely - the power-button NVRAM walk also writes, and
    # this test is only about which ids the colour path selects.
    monkeypatch.setattr(apiv4, "elc_send", lambda fd, payload: None)
    monkeypatch.setattr(apiv4, "begin", lambda fd, cid=0: None)
    monkeypatch.setattr(apiv4, "commit", lambda fd, cid=0: None)
    monkeypatch.setattr(apiv4, "persist", lambda fd, items: None)
    monkeypatch.setattr(apiv4, "set_one_color",
                        lambda fd, rgb, ids: written.append((tuple(rgb), sorted(ids))))
    monkeypatch.setattr(dev, "open_fds", lambda zones: {"elc": 99})
    monkeypatch.setattr(dev, "close_fds", lambda fds: None)

    st = dict(state.DEFAULT_STATE)
    st.update(brightness=255, effect="gradient")
    engine.apply(st, [z for z in device.zone_names() if z != "kbd"])

    ids = sorted(i for _, group in written for i in group)
    assert 0x06 in ids and 0x07 in ids, f"Tron ids never reached the wire: {ids}"


# --------------------------------------- recognition by report shape, not PID
#
# Detection used to pin one product id per controller, so this tool worked on
# exactly one laptop: any other Alienware failed with "not found". Product ids
# differ across models (the chassis answers on 0x0550 as well as 0x0551, Darfon
# keyboards on 0xcabc and 0xdabc as well as 0xd2b1) while the vendor ids do not,
# and the protocol identifies its own generation by report shape. So that is
# what is matched.


def _descriptor(items):
    """Build a minimal HID report descriptor from (tag_byte, value) pairs."""
    out = bytearray()
    for prefix, value, width in items:
        out.append(prefix)
        out += int(value).to_bytes(width, "little") if width else b""
    return bytes(out)


# Report Size (0x75), Report Count (0x95), Report ID (0x85), Output (0x91),
# Feature (0xB1) - one byte of data each.
def _simple_descriptor(report_id, main_tag, size_bits, count):
    return _descriptor([
        (0x85, report_id, 1),
        (0x75, size_bits, 1),
        (0x95, count, 1),
        (main_tag, 0x02, 1),
    ])


def test_the_descriptor_parser_reads_report_sizes():
    """33 payload bytes on report 0 is the chassis signature; 63 on 0xcc is the
    keyboard's. Both measured off the real hardware."""
    import builtins
    from unittest import mock
    desc = _simple_descriptor(0x00, 0x91, 8, 33)
    with mock.patch.object(builtins, "open", mock.mock_open(read_data=desc)):
        assert device.report_map("hidrawX") == {(0x00, "out"): 33}


def test_the_descriptor_parser_survives_rubbish():
    """An unreadable or nonsense descriptor must mean "does not match", never
    an exception - this runs over every HID device on the machine, including
    other people's."""
    import builtins
    from unittest import mock
    for junk in (b"", b"\xff", b"\xfe\x02", b"\x85"):
        with mock.patch.object(builtins, "open", mock.mock_open(read_data=junk)):
            assert isinstance(device.report_map("hidrawX"), dict)


def test_a_chassis_with_a_different_product_id_is_still_recognised(monkeypatch):
    """The portability property, and the reason for the whole change."""
    monkeypatch.setattr(device, "_read_ids", lambda node: (device.ELC_VID, 0x0550))
    monkeypatch.setattr(device, "report_map", lambda node: {(0x00, "out"): 33})
    assert device.node_matches("hidraw3", device.ELC) is True


def test_a_keyboard_with_a_different_product_id_is_still_recognised(monkeypatch):
    """0xcabc and 0xdabc are the x15/x17 Darfon keyboards."""
    monkeypatch.setattr(device, "_read_ids", lambda node: (device.KBD_VID, 0xCABC))
    monkeypatch.setattr(device, "report_map", lambda node: {(0xCC, "feat"): 63})
    assert device.node_matches("hidraw3", device.KBD) is True


def test_the_same_vendor_with_the_wrong_report_is_not_a_match(monkeypatch):
    """Vendor alone would be too loose. Alienware ships more than one HID
    device and this machine carries two unrelated Dell nodes."""
    monkeypatch.setattr(device, "_read_ids", lambda node: (device.ELC_VID, 0x1234))
    monkeypatch.setattr(device, "report_map", lambda node: {(0x00, "out"): 8})
    assert device.node_matches("hidraw3", device.ELC) is False


def test_the_right_report_from_the_wrong_vendor_is_not_a_match(monkeypatch):
    """And shape alone would be too loose in the other direction."""
    monkeypatch.setattr(device, "_read_ids", lambda node: (0x046D, 0xB034))
    monkeypatch.setattr(device, "report_map", lambda node: {(0x00, "out"): 33})
    assert device.node_matches("hidraw3", device.ELC) is False


def test_the_two_controllers_cannot_match_the_same_device(monkeypatch):
    """Their vendors differ, so nothing can be both - which is what lets
    find_node take the first match without tie-breaking."""
    assert device.ELC.vid != device.KBD.vid


def test_nothing_resolves_a_device_by_product_id():
    """A guard against the old habit creeping back. The reference product ids
    are kept for documentation and tests; if a lookup ever uses one again, this
    is the reminder that models differ."""
    import inspect
    source = inspect.getsource(device)
    body = source[source.index("def find_node"):]
    assert "REFERENCE_ELC_PID" not in body
    assert "REFERENCE_KBD_PID" not in body


def test_a_missing_controller_explains_what_it_looked_for(monkeypatch):
    """The error a user on an unsupported machine actually sees, so it should
    say what would have satisfied it."""
    monkeypatch.setattr(device, "find_node", lambda controller: None)
    with pytest.raises(device.DeviceError) as caught:
        device._open_verified(device.KBD)
    message = str(caught.value)
    assert "0d62" in message and "report signature" in message


# ------------------------------------------------ the udev rule is generated
#
# A static rule can only name the product ids of the machine it was written on.
# Detection matches on vendor id and report shape, so it recognises a controller
# whose product id differs - but the rule must then name that id or logind never
# grants the ACL, and the plugin finds the device and cannot open it. That
# presents as "the lights do nothing" with no error anywhere.

def _rule(monkeypatch, found):
    """Run the generator against a fake set of detected controllers."""
    import io, contextlib
    from alienfx_ctl import cli
    monkeypatch.setattr(device, "node_ids", lambda c: found.get(c.key))
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = cli.cmd_udev_rule(None)
    return code, out.getvalue()


def test_the_rule_names_the_ids_actually_present(monkeypatch):
    """Not the reference machine's. This is the whole point."""
    code, text = _rule(monkeypatch, {"elc": (0x187C, 0x0550), "kbd": (0x0D62, 0xCABC)})
    assert code == 0
    assert 'ATTRS{idVendor}=="187c", ATTRS{idProduct}=="0550"' in text
    assert 'ATTRS{idVendor}=="0d62", ATTRS{idProduct}=="cabc"' in text
    assert "0551" not in text and "d2b1" not in text, "leaked the reference ids"


def test_every_generated_line_grants_only_uaccess(monkeypatch):
    """No MODE, no GROUP, no OWNER: the ACL is logind's to hand to the seat
    user, and widening it would grant more than this plugin needs."""
    _, text = _rule(monkeypatch, {"elc": (0x187C, 0x0551), "kbd": (0x0D62, 0xD2B1)})
    rules = [l for l in text.splitlines() if l.startswith("SUBSYSTEM")]
    assert rules
    for line in rules:
        assert line.endswith('TAG+="uaccess"')
        for widening in ("MODE=", "GROUP=", "OWNER="):
            assert widening not in line


def test_a_machine_with_only_one_controller_still_gets_a_rule(monkeypatch):
    """Four-zone models have no per-key keyboard at all."""
    code, text = _rule(monkeypatch, {"elc": (0x187C, 0x0550)})
    assert code == 0
    assert text.count('SUBSYSTEM=="hidraw"') == 1
    assert "0550" in text


def test_finding_nothing_fails_rather_than_writing_an_empty_rule(monkeypatch):
    """The installer falls back to the bundled rule on a non-zero exit. An
    empty file installed to /etc would look like success and grant nothing."""
    code, text = _rule(monkeypatch, {})
    assert code != 0
    assert 'SUBSYSTEM=="hidraw"' not in text


def test_generation_needs_no_access_to_the_device_node():
    """It runs before the rule exists, so it may only read sysfs - which is
    world readable - and must never try to open /dev/hidraw*."""
    import inspect
    from alienfx_ctl import cli
    source = inspect.getsource(cli.cmd_udev_rule)
    assert "open_fds" not in source and "os.open" not in source
