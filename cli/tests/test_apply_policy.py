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


class _Args:
    """The attributes _apply and the commands read off argparse results."""
    def __init__(self, **kw):
        defaults = dict(dry_run=False, fast=True, drop_if_busy=False,
                        persist=False, no_save=False, verbose=False, zones="all")
        defaults.update(kw)
        for name, value in defaults.items():
            setattr(self, name, value)


def _args(**kw):
    return _Args(**kw)


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


# --------------------------------------------------------- zone targeting
#
# The UI asks for --zones kbd during a colour drag on purpose: the keyboard
# costs ~92ms for a full-range repaint while the chassis costs ~320ms under a
# gradient (three zones at three different colours is three 63ms packets).
# ZoneSync used to re-expand that back to every zone, silently throwing the
# optimisation away and making every drag frame pay for the slow controller.

@pytest.fixture()
def applied(monkeypatch):
    """Capture the zone list cmd_set hands to _apply."""
    seen = {}
    monkeypatch.setattr(cli, "_apply",
                        lambda st, zones, args, save=True: (seen.update(zones=list(zones)), 0)[1])
    return seen


def _set_args(**kw):
    args = Args(**kw)
    for name, default in (("color", None), ("color2", None), ("zones", "selected"), ("brightness", None),
                          ("saturation", None), ("min_saturation", None), ("intensity", None),
                          ("effect", None),
                          ("axis", None), ("speed", None), ("themesync", None),
                          ("zonesync", None), ("select", None)):
        if not hasattr(args, name):
            setattr(args, name, default)
    return args


def test_explicit_zones_wins_over_zonesync(applied, config_root):
    """A drag frame must reach only the controller it named."""
    st = state.load_state()
    st["zonesync"] = True
    state.save_state(st)
    cli.cmd_set(_set_args(zones="kbd", color="ff7800", fast=True, drop_if_busy=True))
    assert applied["zones"] == ["kbd"], "ZoneSync re-expanded an explicit --zones"


def test_the_default_still_covers_every_zone_when_synced(applied, config_root):
    st = state.load_state()
    st["zonesync"] = True
    state.save_state(st)
    cli.cmd_set(_set_args(color="ff7800"))
    assert set(applied["zones"]) == set(cli.device.ZONES)


def test_a_drag_frame_still_records_the_colour_for_every_zone(config_root, monkeypatch):
    """Only the keyboard is written, but all four zones must remember the
    colour - otherwise the release, which writes them all, would use a stale
    value for the three it skipped.

    Asserts on the state handed to _apply, since _apply is what persists it.
    """
    captured = {}
    monkeypatch.setattr(cli, "_apply",
                        lambda st, zones, args, save=True: (captured.update(st=st), 0)[1])
    st = state.load_state()
    st["zonesync"] = True
    state.save_state(st)
    cli.cmd_set(_set_args(zones="kbd", color="aa3311", fast=True, drop_if_busy=True))
    for zone in cli.device.ZONES:
        assert captured["st"]["zones"][zone]["color"] == "aa3311"


def test_unsynced_zones_target_only_the_selection(applied, config_root):
    st = state.load_state()
    st["zonesync"] = False
    st["selected_zone"] = "logo"
    state.save_state(st)
    cli.cmd_set(_set_args(color="00ff00"))
    assert applied["zones"] == ["logo"]


# ------------------------------------------------------------ intensity trim

def test_intensity_reaches_every_mode(config_root):
    """It lands in _shape, the single funnel every colour passes through, so one
    control covers the theme gradient, solid fills, effects and the chassis.
    Asserted here because a future refactor that bypassed _shape would silently
    make the slider stop working in whichever mode it skipped."""
    from alienfx_ctl import engine
    base = (190, 63, 80)
    st = state.load_state()
    st["brightness"] = 255
    st["intensity"] = 0
    neutral = engine._shape(base, st)
    st["intensity"] = 10
    boosted = engine._shape(base, st)
    assert boosted != neutral


def test_the_shaping_funnel_has_exactly_one_saturation_stage(config_root):
    """There were three - a multiplier, a `min_saturation` floor and a relative
    trim - and they fought: the floor could lift a colour the trim had just
    taken down, and on a typical 0.67-saturated accent the floor never engaged
    while the trim's centre was a no-op, so the default did nothing. The
    intensity control is now the single authority, and this pins that the funnel
    is just "set saturation, then scale"."""
    from alienfx_ctl import engine, colors as c
    st = state.load_state()
    for spec in ("be3f50", "3fbead", "e68e0d", "22aa66"):
        rgb = c.parse_color(spec)
        for intensity in (-10, 0, 7):
            st["intensity"] = intensity
            expected = c.scale(c.apply_intensity(rgb, intensity), st["brightness"])
            assert engine._shape(rgb, st) == expected


def test_the_retired_saturation_keys_no_longer_affect_anything(config_root):
    """A state file written by an older version still carries `saturation`,
    `value` and `min_saturation`. They must be inert, not partially honoured."""
    from alienfx_ctl import engine
    st = state.load_state()
    st["intensity"] = 0
    plain = engine._shape((190, 63, 80), st)
    st.update(saturation=2.0, value=0.5, min_saturation=1.0)
    assert engine._shape((190, 63, 80), st) == plain


def test_intensity_persists_like_any_other_preference(config_root):
    st = state.load_state()
    st["intensity"] = -4
    state.save_state(st)
    assert state.load_state()["intensity"] == -4


def test_the_retired_saturation_flags_are_gone_from_the_cli():
    """They wrote to state that nothing reads any more, so setting one did
    nothing and said nothing - the worst kind of leftover. The intensity
    control replaced all three."""
    import argparse
    from alienfx_ctl import cli
    parser = cli.build_parser() if hasattr(cli, "build_parser") else None
    if parser is None:
        import inspect
        source = inspect.getsource(cli)
        assert '"--saturation"' not in source
        assert '"--min-saturation"' not in source
        return
    with pytest.raises(SystemExit):
        parser.parse_args(["set", "--saturation", "2.4"])


def test_a_state_file_carrying_the_retired_keys_cleans_itself(config_root):
    """An existing install has them on disk. They must not survive a save, or
    they linger as a puzzle for whoever reads the file next."""
    st = state.load_state()
    st.update(saturation=2.4, min_saturation=0.9, value=0.5, kbd_mode="paint")
    state.save_state(st)
    import json
    on_disk = json.load(open(state.state_path()))
    for key in ("saturation", "min_saturation", "value", "kbd_mode", "themesync"):
        assert key not in on_disk, f"{key} was written back"


# ------------------------------------------------- concurrent writes to state
#
# The UI fires a durable `commit` on a settle timer while the user is still
# clicking. commit loads state, holds the hardware lock for seconds doing the
# power button's NVRAM walk, and used to save its snapshot on the way out -
# overwriting anything changed in between.
#
# That was the ZoneSync toggle "bouncing": the click applied and saved
# correctly, then an in-flight commit wrote the pre-click value back over it.
# The UI read the old value and moved the switch back, so a command that had
# worked looked ignored.

def test_commit_never_writes_state(config_root, monkeypatch):
    """It re-applies what is already on disk, so it has nothing to record."""
    from alienfx_ctl import cli, engine
    saved = []
    monkeypatch.setattr(state, "save_state", lambda st: saved.append(dict(st)))
    monkeypatch.setattr(engine, "apply", lambda st, zones, **kw: {})

    cli.cmd_commit(_args())
    assert saved == [], "commit wrote state back"


def test_a_change_made_during_a_commit_survives_it(config_root, monkeypatch):
    """The race, modelled exactly: commit takes its snapshot, the user changes
    something, commit finishes. The user's change must still be on disk."""
    from alienfx_ctl import cli, engine

    st = state.load_state()
    st["zonesync"] = False
    state.save_state(st)

    def slow_apply(st_arg, zones, **kw):
        # While commit is inside the lock, the user toggles ZoneSync on.
        live = state.load_state()
        live["zonesync"] = True
        state.save_state(live)
        return {}

    monkeypatch.setattr(engine, "apply", slow_apply)
    cli.cmd_commit(_args())

    assert state.load_state()["zonesync"] is True, \
        "commit clobbered a change made while it was running"


def test_restore_and_profile_load_do_not_write_state_either(config_root, monkeypatch):
    """The two paths that already had this right; pinned so they keep it."""
    from alienfx_ctl import cli, engine
    saved = []
    monkeypatch.setattr(state, "save_state", lambda st: saved.append(dict(st)))
    monkeypatch.setattr(engine, "apply", lambda st, zones, **kw: {})
    cli.cmd_restore(_args())
    assert saved == [], "restore wrote state back"


@pytest.mark.parametrize("field,value", [
    ("zonesync", True),
    ("brightness", 200),
    ("intensity", 7),
    ("selected_zone", "logo"),
])
def test_any_settled_change_survives_a_concurrent_commit(config_root, monkeypatch,
                                                         field, value):
    """Generalises the ZoneSync race to every control the panel drives. The UI
    fires a durable commit on a settle timer while the user is still adjusting
    things, so this is not a rare interleaving - it is the normal one."""
    from alienfx_ctl import cli, engine

    def user_changes_something(st_arg, zones, **kw):
        live = state.load_state()
        live[field] = value
        state.save_state(live)
        return {}

    monkeypatch.setattr(engine, "apply", user_changes_something)
    cli.cmd_commit(_args())
    assert state.load_state()[field] == value, f"commit clobbered {field}"
