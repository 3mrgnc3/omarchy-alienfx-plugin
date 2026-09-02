"""Which applies may be dropped, and which must land.

A colour drag fires changes faster than the hardware can take them, so
intermediate frames are allowed to vanish - the next one supersedes them.
The value the user *settles* on may not: dropping it leaves saved state behind
what the UI is showing, and the next reconciliation drags the control back to
the older number. That was a real regression, so the policy is pinned here.
"""

import pytest

from alienfx_ctl import cli, engine, lock, state


class Args:
    def __init__(self, **kw):
        self.dry_run = False
        self.verbose = False
        self.no_save = False
        self.persist = False
        self.fast = False
        self.drop_if_busy = False
        for key, value in kw.items():
            setattr(self, key, value)


@pytest.fixture()
def spy(monkeypatch):
    """Record how _apply asks for the lock, without touching hardware."""
    calls = {}

    class FakeLock:
        def __init__(self, wait, drop_if_busy):
            calls["wait"] = wait
            calls["drop_if_busy"] = drop_if_busy
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(cli.lock, "hardware_lock",
                        lambda wait, drop_if_busy: FakeLock(wait, drop_if_busy))
    monkeypatch.setattr(cli.engine, "apply",
                        lambda st, zones, persist=False, fast=False: (
                            calls.update(fast=fast), {"effect": "solid", "zones": zones,
                                                      "elc": {}, "kbd_leds": None,
                                                      "kbd_effect": None, "kbd_solid": None,
                                                      "power_programmed": None})[1])
    monkeypatch.setattr(cli.state, "save_state", lambda st: calls.update(saved=True))
    return calls


def test_an_intermediate_frame_is_droppable(spy, config_root):
    cli._apply(state.load_state(), ["kbd"], Args(fast=True, drop_if_busy=True))
    assert spy["drop_if_busy"] is True
    assert spy["wait"] == 0.0


def test_a_final_value_waits_for_the_lock(spy, config_root):
    """--fast means 'skip the slow NVRAM walk', NOT 'may be discarded'.
    Conflating the two is what lost release values."""
    cli._apply(state.load_state(), ["kbd"], Args(fast=True))
    assert spy["drop_if_busy"] is False
    assert spy["wait"] > 0
    assert spy["fast"] is True, "it should still skip power programming"


def test_a_durable_apply_waits_and_programs(spy, config_root):
    cli._apply(state.load_state(), ["kbd"], Args())
    assert spy["drop_if_busy"] is False
    assert spy["fast"] is False


def test_a_dropped_frame_reports_success_and_saves_nothing(monkeypatch, config_root):
    """A dropped frame must be invisible: no error for the UI to show, and no
    state write, or saved state would claim a value that never reached the
    hardware."""
    monkeypatch.setattr(cli.lock, "hardware_lock",
                        lambda wait, drop_if_busy: (_ for _ in ()).throw(lock.Busy("busy")))
    saved = []
    monkeypatch.setattr(cli.state, "save_state", lambda st: saved.append(st))
    code = cli._apply(state.load_state(), ["kbd"], Args(fast=True, drop_if_busy=True))
    assert code == 0
    assert saved == []


def test_a_blocked_final_value_reports_failure(monkeypatch, config_root):
    """If a deliberate change genuinely cannot land, say so rather than
    pretending it worked."""
    monkeypatch.setattr(cli.lock, "hardware_lock",
                        lambda wait, drop_if_busy: (_ for _ in ()).throw(lock.Busy("busy")))
    assert cli._apply(state.load_state(), ["kbd"], Args(fast=True)) != 0
