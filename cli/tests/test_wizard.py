"""The KeyMap Wizard.

The wizard is the one part of this project a user only ever runs once, on a
machine nobody has tested, with no keymap to fall back on - so it gets driven
end to end here rather than trusted. The flow is exercised through a scripted
key sequence against a fake keyboard, which is the same seam a terminal drives.
"""

import json

import pytest

from alienfx_ctl import gradient, keymap, layout, term, wizard


# --------------------------------------------------------------- key input

def test_arrow_keys_decode_from_escape_sequences():
    """Arrows arrive as three bytes; nothing else in the wizard sees them."""
    import io
    stream = io.StringIO("\x1b[A\x1b[B\x1b[C\x1b[D")
    assert [term.read_key(stream) for _ in range(4)] == [
        term.UP, term.DOWN, term.RIGHT, term.LEFT]


def test_named_keys_and_plain_characters_decode():
    import io
    stream = io.StringIO("\r q\x7f\x03")
    assert [term.read_key(stream) for _ in range(5)] == [
        term.ENTER, term.SPACE, "q", term.BACKSPACE, term.INTERRUPT]


def test_end_of_input_reads_as_empty_rather_than_blocking():
    """The signal the wizard stops on, so a closed terminal ends the flow
    instead of spinning."""
    import io
    assert term.read_key(io.StringIO("")) == ""


def test_raw_mode_reports_when_there_is_no_terminal():
    """A pipe must not be put in raw mode, and the caller has to be able to
    tell - the mapping step is unusable without a terminal."""
    import io
    with term.raw_mode(io.StringIO("")) as raw:
        assert raw is False


# ------------------------------------------------------------- the layout

def test_the_builtin_template_matches_the_shipped_keymap(shipped_keymap):
    """The template is derived from the shipped keymap, not a second copy of
    the layout written out by hand - so it cannot drift."""
    grid = layout.Layout.from_keymap(shipped_keymap)
    assert grid.grid_positions() == shipped_keymap["grid_positions"]
    assert len(grid.keys()) == len(shipped_keymap["key_to_index"])


def test_the_template_keeps_real_column_positions(shipped_keymap):
    """Columns are physical positions, not sequence numbers. The blend depends
    on it: the media keys reach column 16 while the bottom row stops at 13."""
    grid = layout.Layout.from_keymap(shipped_keymap)
    widest = max(col for row in grid.rows for _, col in row)
    assert widest == 16
    bottom = max(col for _, col in grid.rows[-1])
    assert bottom < widest, "a ragged grid, which is what a real keyboard is"


def test_vertical_movement_tracks_the_column_not_the_list_position(shipped_keymap):
    """Rows have different key counts, so moving down must land under the key
    physically below rather than at the same index in a shorter row."""
    grid = layout.Layout.from_keymap(shipped_keymap)
    cursor = (0, 0)
    for _ in range(len(grid.rows) - 1):
        cursor = grid.move(cursor, term.DOWN)
        column = grid.rows[cursor[0]][cursor[1]][1]
        assert column <= 1, f"drifted to column {column}"


def test_movement_stops_at_the_edges(shipped_keymap):
    grid = layout.Layout.from_keymap(shipped_keymap)
    assert grid.move((0, 0), term.UP) == (0, 0)
    assert grid.move((0, 0), term.LEFT) == (0, 0)
    last_row = len(grid.rows) - 1
    assert grid.move((last_row, 0), term.DOWN)[0] == last_row


def test_a_layout_can_be_typed_out_when_there_is_no_template():
    grid = layout.Layout.from_rows([["esc", "f1"], ["a", "s", "d"]])
    assert grid.keys() == ["esc", "f1", "a", "s", "d"]
    assert grid.grid_positions()["d"] == {"row": 1, "col": 2}


def test_an_empty_layout_is_rejected():
    with pytest.raises(layout.LayoutError):
        layout.Layout([])
    with pytest.raises(layout.LayoutError):
        layout.Layout.from_keymap({})


def test_the_cursor_and_assigned_keys_are_visible_in_the_drawing(shipped_keymap):
    grid = layout.Layout.from_keymap(shipped_keymap)
    drawn = grid.render(cursor=(0, 0), assigned={"f1": 1})
    assert "[esc ]" in drawn, "the cursor is bracketed"
    assert " f1 ." in drawn, "an assigned key is marked"


# ------------------------------------------------------- the mapping flow

@pytest.fixture()
def grid_with_hints():
    """A small grid whose first key carries an Fn legend."""
    return layout.Layout([[("f7", 0)], [("f8", 0)]], {"f7": "kbd_backlight"})


@pytest.fixture()
def grid():
    """A small grid, so a scripted run stays readable."""
    return layout.Layout.from_rows([["esc", "f1", "f2"], ["a", "s", "d"]])


def _run(grid, keys, **kwargs):
    """Drive assign_leds with a scripted key sequence.

    Records which LED was lit red and in what order, which is what the user
    physically sees, and the saved snapshots handed to ``on_save``.
    """
    probed, saves = [], []
    def paint(changes):
        for index, red, green, blue in changes:
            if (red, green, blue) == wizard.PROBE_COLOUR:
                probed.append(index)
    assigned = wizard.assign_leds(
        grid, paint, term.from_sequence(keys), lambda line: None,
        on_save=saves.append, **kwargs)
    return assigned, probed, saves


def test_the_first_key_is_named_and_its_guess_is_lit_red(grid):
    """The core interaction, in the direction that matters: the terminal names
    a key, the hardware lights a guess, the user confirms."""
    assigned, probed, _ = _run(grid, [term.ENTER, "q"])
    assert assigned == {"esc": 0}
    assert probed[0] == 0, "a candidate LED was lit before anything was asked"


def test_left_and_right_walk_the_light_onto_the_key(grid):
    """Left/right adjust the LED index - the thing the user cannot know - while
    the key being mapped stays put."""
    assigned, probed, _ = _run(grid, [term.RIGHT, term.RIGHT, term.ENTER, "q"])
    assert assigned == {"esc": 2}, "still mapping esc, but onto LED 2"
    # The trailing entry is the *next* key's guess, lit before the quit landed.
    assert probed[:3] == [0, 1, 2], "the light moved one LED at a time"


def test_left_stops_at_zero_and_right_stops_at_the_last_led(grid):
    assigned, _, _ = _run(grid, [term.LEFT, term.LEFT, term.ENTER, "q"])
    assert assigned == {"esc": 0}
    assigned, _, _ = _run(grid, [term.RIGHT, term.RIGHT, term.ENTER, "q"], max_index=1)
    assert assigned == {"esc": 1}


def test_page_keys_jump_by_ten(grid):
    """A guess that is far out should not need ten presses to fix."""
    assigned, _, _ = _run(grid, [term.PAGE_DOWN, term.ENTER, "q"])
    assert assigned == {"esc": 10}
    assigned, _, _ = _run(grid, [term.PAGE_DOWN, term.PAGE_UP, term.ENTER, "q"])
    assert assigned == {"esc": 0}


def test_up_and_down_choose_which_key_is_being_mapped(grid):
    """The other axis: down moves to the next key in reading order, which is
    how the user says "not this one, the next one"."""
    assigned, _, _ = _run(grid, [term.DOWN, term.DOWN, term.ENTER, "q"])
    assert assigned == {"f2": 0}, "skipped forward to the third key"


def test_the_guess_follows_the_last_confirmed_led(grid):
    """The strip runs in reading order, so last+1 is right almost every time.
    This is what makes eighty-five keys a matter of pressing Enter."""
    assigned, probed, _ = _run(grid, [term.ENTER, term.ENTER, term.ENTER, "q"])
    assert assigned == {"esc": 0, "f1": 1, "f2": 2}
    assert probed[:3] == [0, 1, 2], "each key guessed the next LED unaided"


def test_correcting_one_offset_corrects_every_later_guess(grid):
    """The property that makes a machine whose indices start at 3 cost one
    correction instead of eighty-five."""
    assigned, _, _ = _run(grid, [term.RIGHT, term.RIGHT, term.RIGHT, term.ENTER,
                                 term.ENTER, term.ENTER, "q"])
    assert assigned == {"esc": 3, "f1": 4, "f2": 5}


def test_confirming_turns_the_key_green_and_saves_progress(grid):
    """Saved after each key, so a crash or a closed window costs one key."""
    assigned, _, saves = _run(grid, [term.ENTER, term.ENTER, "q"])
    assert [sorted(snapshot) for snapshot in saves] == [["esc"], ["esc", "f1"]]
    assert assigned == {"esc": 0, "f1": 1}


def test_a_confirmed_key_is_painted_green(grid):
    greens = []
    def paint(changes):
        for index, red, green, blue in changes:
            if (red, green, blue) == wizard.CONFIRMED_COLOUR:
                greens.append(index)
    wizard.assign_leds(grid, paint, term.from_sequence([term.ENTER, "q"]),
                       lambda line: None)
    assert greens == [0]


def test_skipping_moves_on_without_consuming_an_led(grid):
    """A machine without this key: the next key should still guess LED 0,
    because nothing has been confirmed yet."""
    assigned, probed, _ = _run(grid, ["s", term.ENTER, "q"])
    assert assigned == {"f1": 0}, "esc skipped, f1 took the LED"


def test_a_key_can_be_unassigned_and_reassigned(grid):
    assigned, _, _ = _run(grid, [term.ENTER, term.UP, "u", "q"])
    assert assigned == {}
    assigned, _, _ = _run(grid, [term.ENTER, term.UP, "u", term.ENTER, "q"])
    assert assigned == {"esc": 0}


def test_revisiting_a_key_shows_the_led_it_already_has(grid):
    """Going back to a saved key must offer its saved index, not a fresh
    guess - otherwise correcting a mistake means finding it all over again."""
    assigned, probed, _ = _run(grid, [term.RIGHT, term.RIGHT, term.ENTER,
                                      term.UP, "q"])
    assert assigned == {"esc": 2}
    assert probed[-1] == 2, "came back to the saved LED"


def test_an_existing_mapping_is_resumed_rather_than_restarted(grid):
    """Re-running the wizard should not ask again for keys already done."""
    assigned, _, _ = _run(grid, [term.ENTER, "q"], assigned={"esc": 4})
    assert assigned["esc"] == 4, "the saved value was offered back, not overwritten"


def test_unknown_keys_are_ignored_rather_than_assigning_something(grid):
    """A stray keypress must not silently map the wrong key."""
    assigned, probed, _ = _run(grid, ["z", "!", term.ENTER, "q"])
    assert assigned == {"esc": 0}
    assert probed.count(0) == 1, "the stray presses neither moved the light nor assigned"


@pytest.mark.parametrize("quit_key", ["q", term.ESCAPE, term.INTERRUPT, ""])
def test_every_way_out_finishes_cleanly(grid, quit_key):
    """Including Ctrl-C and a closed terminal - the wizard runs with the
    terminal in raw mode, so an unhandled exit there leaves a broken shell."""
    assigned, _, _ = _run(grid, [term.ENTER, quit_key])
    assert assigned == {"esc": 0}


def test_the_flow_ends_after_the_last_key(grid):
    """It must stop when the keyboard is done rather than looping forever."""
    assigned, _, _ = _run(grid, [term.ENTER] * len(grid.keys()))
    assert len(assigned) == len(grid.keys())


def test_the_whole_shipped_keyboard_can_be_mapped_by_pressing_enter(shipped_keymap):
    """The end-to-end usability claim: on a machine matching the template, the
    guesses are right and mapping all 85 keys is 85 presses of Enter."""
    grid = layout.Layout.from_keymap(shipped_keymap)
    assigned, _, _ = _run(grid, [term.ENTER] * len(grid.keys()))
    assert len(assigned) == 85
    assert assigned == {name: index for index, name in enumerate(grid.keys())}


# ------------------------------------------------------------- the output

def test_a_finished_mapping_saves_a_keymap_the_renderer_can_use(config_root, grid, monkeypatch):
    """The end-to-end contract: what the wizard writes must load, validate, and
    render a gradient. A keymap that saves but cannot drive the keyboard is the
    failure that matters here."""
    monkeypatch.setattr(wizard.hardware, "model", lambda: "Alienware Test 1")
    monkeypatch.setattr(wizard.hardware, "slug", lambda name="": "test-1")
    assigned = {"esc": 0, "f1": 1, "f2": 2, "a": 10, "s": 11, "d": 12}

    assert wizard._save("Alienware Test 1", grid, assigned, {"logo": 0}) == 0

    written = json.load(open(keymap.model_keymap_path("Alienware Test 1")))
    keymap.validate(written)
    assert written["key_to_index"] == assigned
    assert written["index_to_key"]["10"] == "a"
    assert written["zones"] == {"logo": 0}
    assert written["total_mapped"] == 6

    leds = gradient.render_kbd(written, (255, 0, 0), (0, 0, 255))
    assert len(leds) == len(assigned)
    assert leds[0][1:] == (255, 0, 0), "the near corner is the near anchor"


def test_a_partial_mapping_still_saves_and_only_records_what_was_assigned(config_root, grid, monkeypatch):
    """Better to keep an hour of pointing than to refuse an incomplete map; the
    renderer paints named keys and leaves the rest alone."""
    monkeypatch.setattr(wizard.hardware, "slug", lambda name="": "test-1")
    assert wizard._save("Test", grid, {"esc": 0, "d": 5}, {}) == 0
    written = json.load(open(keymap.model_keymap_path("Test")))
    assert set(written["key_to_index"]) == {"esc", "d"}
    assert set(written["grid_positions"]) == {"esc", "d"}
    assert "zones" not in written


def test_the_saved_grid_keeps_the_real_columns_from_the_template(config_root, shipped_keymap, monkeypatch):
    """The reason to start from a template: naming keys cannot express a gap or
    a wide key, and the diagonal blend reads those columns."""
    monkeypatch.setattr(wizard.hardware, "slug", lambda name="": "m16-r2")
    grid = layout.Layout.from_keymap(shipped_keymap)
    assigned = dict(shipped_keymap["key_to_index"])
    assert wizard._save("Alienware m16 R2", grid, assigned, {}) == 0
    written = json.load(open(keymap.model_keymap_path("Alienware m16 R2")))
    assert written["grid_positions"] == shipped_keymap["grid_positions"]
    assert gradient.extreme_indices(written) == gradient.extreme_indices(shipped_keymap)


# --------------------------------------------------- the bundled layouts
#
# Bundled layouts carry keyboard *shapes* and deliberately no LED indices.
# Indices are irregular on real hardware and cannot be derived - see
# docs/dead-ends.md - so the wizard probes them. The shape is bundled because
# without it there is no way to map a keyboard the reference does not have, such
# as one with a numeric keypad.

def test_every_bundled_layout_loads_and_is_navigable():
    for name in layout.bundled():
        grid = layout.load_bundled(name)
        assert grid.keys(), f"{name} is empty"
        cursor = (0, 0)
        for direction in (term.DOWN, term.RIGHT, term.UP, term.LEFT):
            cursor = grid.move(cursor, direction)
        assert grid.name_at(cursor)


def test_no_bundled_layout_claims_to_know_an_led_index():
    """The one thing that must never be shipped as a guess. A wrong shape is
    visible and skippable; a wrong index silently lights the wrong key, and the
    user cannot tell our bad data from their own hardware."""
    import json
    with open(layout.BUNDLED_LAYOUTS, encoding="utf-8") as handle:
        raw = json.load(handle)
    for name, spec in raw["layouts"].items():
        for row in spec["rows"]:
            for entry in row:
                assert len(entry) == 2, f"{name}: unexpected extra field {entry}"
                assert isinstance(entry[0], str) and isinstance(entry[1], int)
        assert "key_to_index" not in spec and "index" not in spec


def test_the_reference_layout_still_matches_the_shipped_keymap(shipped_keymap):
    """It is generated from that keymap, so this catches the two drifting
    apart - which would mean two descriptions of one keyboard."""
    assert (layout.load_bundled("m16-r2").grid_positions()
            == layout.Layout.from_keymap(shipped_keymap).grid_positions())


def test_every_layout_is_honest_about_whether_it_was_tested():
    """Only the machine we actually own is marked verified. The others are
    inferred, and the wizard says so when it offers them."""
    verified = {name for name, spec in layout.bundled().items() if spec.get("verified")}
    assert verified == {"m16-r2"}
    for name, spec in layout.bundled().items():
        assert spec.get("source"), f"{name} does not say where it came from"


def test_the_numpad_layout_actually_contains_a_numpad():
    """The reason bundling shapes is necessary rather than merely convenient:
    a numpad user cannot map keys the template does not contain."""
    keys = set(layout.load_bundled("numpad").keys())
    assert {"kp0", "kp5", "kpenter", "numlock"} <= keys
    assert len(keys) > len(set(layout.load_bundled("m16-r2").keys()))


def test_the_compact_layout_drops_only_the_media_column():
    reference = set(layout.load_bundled("m16-r2").keys())
    compact = set(layout.load_bundled("compact").keys())
    assert compact < reference
    assert reference - compact == {"micmute", "mute", "volumeup", "volumedown"}


def test_every_key_in_every_layout_has_a_readable_label():
    """The user is reading these off their own keyboard, so a raw internal name
    like 'kpasterisk' shouted back at them is a usability bug."""
    for name in layout.bundled():
        for key in layout.load_bundled(name).keys():
            assert layout.label_for(key), key
            # Either it has a written label, or its own name is already short
            # and self-evident ("q", "f12", "1"). What must never happen is a
            # long internal identifier shown raw - "kpasterisk" means nothing
            # to someone looking at their keyboard.
            assert key in layout.LABELS or (key.isalnum() and len(key) <= 3), (
                f"{key!r} would be shown to the user as-is")


# ------------------------------------------------ the whole round trip
#
# Verified by hand once and then guarded here, because every part of it can
# pass on its own while the chain is broken: a keymap that saves to a path the
# loader does not read presents as "the wizard ran and changed nothing".

def test_pointing_at_keys_produces_a_keymap_the_renderer_then_uses(
        config_root, shipped_keymap, monkeypatch):
    """assign_leds -> _save -> keymap.load() -> a rendered gradient."""
    from alienfx_ctl import engine, gradient, hardware, keymap, state
    monkeypatch.setattr(hardware, "model", lambda: "Alienware Test 9")
    monkeypatch.setattr(hardware, "slug", lambda text="": "test-9")
    monkeypatch.setattr(hardware, "keymap_filename",
                        lambda text="": "alienware-test-9-keymap.json")

    grid = layout.Layout.from_keymap(shipped_keymap)
    lit = []
    assigned = wizard.assign_leds(
        grid, lambda changes: lit.extend(changes),
        term.from_sequence([term.ENTER] * len(grid.keys())),
        lambda line: None)
    assert len(assigned) == len(grid.keys())

    assert wizard._save("Alienware Test 9", grid, assigned, {"logo": 0, "pbtn": 4}) == 0

    # The loader must find it without being told where to look.
    assert keymap.has_user_keymap() is True
    loaded = keymap.load()
    assert loaded["device"] == "Alienware Test 9"
    assert loaded["key_to_index"] == assigned

    # And the renderer must be able to drive it.
    leds = gradient.render_kbd(loaded, (255, 0, 0), (0, 0, 255))
    assert len(leds) == len(assigned)
    assert keymap.zones(loaded) == {"logo": [0], "pbtn": [4]}

    st = dict(state.DEFAULT_STATE)
    st.update(brightness=255, effect="gradient")
    plan = engine.plan(st, ["kbd", "logo", "pbtn"])
    assert plan["kbd_leds"] and set(plan["elc"]) == {"logo", "pbtn"}


def test_the_wizard_records_the_product_id_it_actually_found(config_root, monkeypatch):
    """Product ids differ across models, so writing a constant would mislabel
    every keymap made on other hardware."""
    from alienfx_ctl import device, hardware
    monkeypatch.setattr(hardware, "slug", lambda text="": "test-9")
    monkeypatch.setattr(device, "node_ids", lambda controller: (0x0D62, 0xCABC))
    grid = layout.Layout.from_rows([["esc", "f1"]])
    assert wizard._save("Test", grid, {"esc": 0}, {}) == 0
    import json
    from alienfx_ctl import keymap
    written = json.load(open(keymap.model_keymap_path("Test")))
    assert written["vid_pid"] == "0d62:cabc"


def test_a_keymap_saved_without_a_detectable_device_still_loads(config_root, monkeypatch):
    """The wizard can be run to import a layout on a machine whose controller
    is not present. That should not produce an unloadable file."""
    from alienfx_ctl import device, hardware, keymap
    monkeypatch.setattr(hardware, "slug", lambda text="": "test-9")
    monkeypatch.setattr(device, "node_ids", lambda controller: None)
    grid = layout.Layout.from_rows([["esc", "f1"]])
    assert wizard._save("Test", grid, {"esc": 0}, {}) == 0
    keymap.validate(keymap.load_file(keymap.model_keymap_path("Test")))


# ---------------------------------------------------------- the Fn legends
#
# secondary_functions sat in the shipped keymap read by nothing, and a wizard
# re-run dropped it silently - so the bundled keymap was richer than anything
# the wizard could produce. It is real data about the keyboard, and it is
# exactly the kind of hint that makes a key identifiable: "F7" alone is not how
# anyone finds the key they are looking at.

def test_the_reference_layout_carries_the_fn_legends():
    grid = layout.load_bundled("m16-r2")
    assert grid.hints["f7"] == "kbd_backlight"
    assert grid.hints["f12"] == "touchpad_toggle"
    assert len(grid.hints) > 20


def test_placeholder_legends_are_not_carried():
    """The shipped file uses "." for keys with no secondary function; showing
    that to the user would be worse than showing nothing."""
    grid = layout.load_bundled("m16-r2")
    assert "esc" not in grid.hints
    assert all(value.strip() not in ("", ".") for value in grid.hints.values())


def test_legends_come_from_the_keymap_not_from_layouts_json():
    """One description of what each key also does. layouts.json carries shapes
    only, so it cannot drift from the keymap on this."""
    import json
    with open(layout.BUNDLED_LAYOUTS, encoding="utf-8") as handle:
        raw = json.load(handle)
    assert "secondary_functions" not in json.dumps(raw)
    assert layout.load_bundled("m16-r2").hints, "but they still arrive"


def test_a_key_the_reference_lacks_simply_has_no_legend():
    grid = layout.load_bundled("numpad")
    assert "kp5" not in grid.hints
    assert grid.hints.get("f7") == "kbd_backlight"


def test_the_legend_is_shown_while_mapping(grid_with_hints):
    lines = []
    wizard.assign_leds(grid_with_hints, lambda changes: None,
                       term.from_sequence(["q"]), lines.append)
    assert any("Fn: kbd_backlight" in line for line in lines)


def test_a_key_without_a_legend_shows_no_bracket(grid_with_hints):
    lines = []
    wizard.assign_leds(grid_with_hints, lambda changes: None,
                       term.from_sequence([term.DOWN, "q"]), lines.append)
    shown = [line for line in lines if "Find this key" in line][-1]
    assert "Fn:" not in shown


def test_a_re_run_preserves_the_legends(config_root, monkeypatch):
    """The actual bug: they were silently dropped on save."""
    import json
    from alienfx_ctl import hardware, keymap
    monkeypatch.setattr(hardware, "slug", lambda text="": "test-9")
    grid = layout.load_bundled("m16-r2")
    assigned = {name: index for index, name in enumerate(grid.keys())}
    assert wizard._save("Test", grid, assigned, {}) == 0
    written = json.load(open(keymap.model_keymap_path("Test")))
    assert written["secondary_functions"]["f7"] == "kbd_backlight"


def test_only_the_legends_of_mapped_keys_are_written(config_root, monkeypatch):
    from alienfx_ctl import hardware, keymap
    monkeypatch.setattr(hardware, "slug", lambda text="": "test-9")
    grid = layout.load_bundled("m16-r2")
    assert wizard._save("Test", grid, {"f7": 0}, {}) == 0
    import json
    written = json.load(open(keymap.model_keymap_path("Test")))
    assert set(written["secondary_functions"]) == {"f7"}


def test_a_layout_with_no_legends_omits_the_field_entirely(config_root, monkeypatch):
    from alienfx_ctl import hardware, keymap
    monkeypatch.setattr(hardware, "slug", lambda text="": "test-9")
    grid = layout.Layout.from_rows([["esc", "f1"]])
    assert wizard._save("Test", grid, {"esc": 0}, {}) == 0
    import json
    written = json.load(open(keymap.model_keymap_path("Test")))
    assert "secondary_functions" not in written


def test_the_shipped_keymap_survives_a_full_round_trip(config_root, shipped_keymap, monkeypatch):
    """Load the reference keymap, re-save it through the wizard, and get the
    same legends back - so re-running the wizard is lossless."""
    import json
    from alienfx_ctl import hardware, keymap
    monkeypatch.setattr(hardware, "slug", lambda text="": "m16-r2")
    grid = layout.Layout.from_keymap(shipped_keymap)
    assert wizard._save("Alienware m16 R2", grid,
                        dict(shipped_keymap["key_to_index"]), {}) == 0
    written = json.load(open(keymap.model_keymap_path("Alienware m16 R2")))
    original = {k: v for k, v in shipped_keymap["secondary_functions"].items()
                if v and v.strip() != "."}
    assert written["secondary_functions"] == original
