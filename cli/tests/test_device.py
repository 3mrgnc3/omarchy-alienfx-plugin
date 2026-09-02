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
    assert device.find_node(device.KBD_VID, device.KBD_PID) == "/dev/hidrawX"


def test_find_node_returns_none_for_an_unknown_device(monkeypatch):
    monkeypatch.setattr(device, "iter_nodes", lambda: iter([("hidraw0", 0x1234, 0x5678)]))
    assert device.find_node(0xDEAD, 0xBEEF) is None


def test_verify_node_refuses_a_mismatched_device(monkeypatch):
    """The guard that stops lighting packets reaching an unrelated device."""
    monkeypatch.setattr(device, "_read_ids", lambda node: (0x1050, 0x0407))
    with pytest.raises(device.DeviceError) as excinfo:
        device.verify_node("/dev/hidraw0", device.ELC_VID, device.ELC_PID)
    assert "refusing to write" in str(excinfo.value)


def test_verify_node_accepts_a_match(monkeypatch):
    monkeypatch.setattr(device, "_read_ids", lambda node: (device.ELC_VID, device.ELC_PID))
    device.verify_node("/dev/hidraw1", device.ELC_VID, device.ELC_PID)


def test_verify_node_refuses_when_identity_is_unknown(monkeypatch):
    monkeypatch.setattr(device, "_read_ids", lambda node: None)
    with pytest.raises(device.DeviceError):
        device.verify_node("/dev/hidraw9", device.ELC_VID, device.ELC_PID)


def test_open_fds_is_atomic_and_leaks_nothing(monkeypatch):
    """If the second controller fails to open, the first must be closed again -
    otherwise a retry loop leaks descriptors and a scheme half-applies."""
    opened, closed = [], []

    def fake_open(vid, pid, label):
        if (vid, pid) == (device.KBD_VID, device.KBD_PID):
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

    def fake_open(vid, pid, label):
        calls.append(label)
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
