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


# ------------------------------------------------ the keyboard shape
#
# The base shape is read from the shipped keymap at runtime rather than stored
# as data, so there is one description of that keyboard instead of two that can
# drift. Only genuinely extra keys are bundled.
#
# An earlier version shipped three whole layouts and they carried 17 keys of
# information between them: one was byte-identical to the shipped keymap's grid
# and another was that minus four media keys, which a user without them skips in
# four keypresses.

def test_the_reference_shape_is_derived_from_the_shipped_keymap(shipped_keymap):
    """Not a copy of it. If it were stored separately the two would need a test
    whose only job was policing the fact that they matched - which is what there
    used to be."""
    assert layout.reference().grid_positions() == shipped_keymap["grid_positions"]


def test_the_bundled_data_holds_no_key_the_reference_already_has():
    """The duplication check. Anything already described by the keymap must not
    be restated here."""
    base = set(layout.reference().keys())
    for name, spec in layout.extensions().items():
        overlap = {key for key, _row, _col in spec["keys"]} & base
        assert not overlap, f"{name} restates keys the keymap already has: {sorted(overlap)}"


def test_an_extension_adds_its_keys_and_keeps_the_originals():
    base = layout.reference()
    extended = layout.extend(base, "numpad")
    assert set(base.keys()) < set(extended.keys())
    assert {"kp0", "kp5", "kpenter", "numlock"} <= set(extended.keys())


def test_an_extension_keeps_the_fn_legends():
    assert layout.extend(layout.reference(), "numpad").hints == layout.reference().hints


def test_extension_keys_are_sorted_into_place_by_column():
    """A keypad has to land to the right of the keys already on that row, or the
    drawn keyboard does not match the real one."""
    row = layout.extend(layout.reference(), "numpad").rows[0]
    columns = [col for _name, col in row]
    assert columns == sorted(columns)
    assert row[-1][0].startswith("kp") or row[-1][0] == "numlock"


def test_an_unknown_extension_is_refused():
    with pytest.raises(layout.LayoutError):
        layout.extend(layout.reference(), "trackball")


def test_no_bundled_shape_claims_to_know_an_led_index():
    """The one thing that must never be shipped as a guess. A wrong shape is
    visible and skippable; a wrong index silently lights the wrong key, and the
    user cannot tell our bad data from their own hardware."""
    import json
    with open(layout.LAYOUT_EXTENSIONS, encoding="utf-8") as handle:
        raw = json.load(handle)
    for name, spec in raw["extensions"].items():
        for entry in spec["keys"]:
            assert len(entry) == 3, f"{name}: unexpected field in {entry}"
            key, row, col = entry
            assert isinstance(key, str) and isinstance(row, int) and isinstance(col, int)
        assert "key_to_index" not in spec and "index" not in spec


def test_every_extension_is_honest_about_whether_it_was_tested():
    """Nothing here has been run against a machine that has one."""
    for name, spec in layout.extensions().items():
        assert spec.get("label"), f"{name}: no label to show the user"
        assert spec.get("source"), f"{name}: does not say where it came from"
        assert spec.get("verified") is False, f"{name}: claims to be verified"


def test_every_key_offered_has_a_readable_label():
    """The user is reading these off their own keyboard, so a raw internal name
    like 'kpasterisk' shouted back at them is a usability bug."""
    grid = layout.extend(layout.reference(), "numpad")
    for key in grid.keys():
        assert layout.label_for(key), key
        assert key in layout.LABELS or (key.isalnum() and len(key) <= 3), (
            f"{key!r} would be shown to the user as-is")


def test_a_keyboard_missing_some_keys_needs_no_special_shape():
    """Why `compact` was deleted rather than kept. A machine without the media
    column skips four keys; it does not need a layout of its own."""
    grid = layout.reference()
    assigned, _, _ = _run(grid, ["s"] * 4 + [term.ENTER, "q"])
    assert len(assigned) == 1, "skipping is the mechanism"


# ---------------------------------------------------------- the Fn legends
#
# secondary_functions sat in the shipped keymap read by nothing, and a wizard
# re-run dropped it silently - so the bundled keymap was richer than anything
# the wizard could produce. It is real data about the keyboard, and it is
# exactly the kind of hint that makes a key identifiable: "F7" alone is not how
# anyone finds the key they are looking at.

def test_the_reference_layout_carries_the_fn_legends():
    grid = layout.reference()
    assert grid.hints["f7"] == "kbd_backlight"
    assert grid.hints["f12"] == "touchpad_toggle"
    assert len(grid.hints) > 20


def test_placeholder_legends_are_not_carried():
    """The shipped file uses "." for keys with no secondary function; showing
    that to the user would be worse than showing nothing."""
    grid = layout.reference()
    assert "esc" not in grid.hints
    assert all(value.strip() not in ("", ".") for value in grid.hints.values())


def test_legends_come_from_the_keymap_not_from_the_bundled_data():
    """One description of what each key also does, so the two cannot drift."""
    import json
    with open(layout.LAYOUT_EXTENSIONS, encoding="utf-8") as handle:
        raw = json.load(handle)
    assert "secondary_functions" not in json.dumps(raw)
    assert layout.reference().hints, "but they still arrive"


def test_a_key_the_reference_lacks_simply_has_no_legend():
    grid = layout.extend(layout.reference(), "numpad")
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
    grid = layout.reference()
    assigned = {name: index for index, name in enumerate(grid.keys())}
    assert wizard._save("Test", grid, assigned, {}) == 0
    written = json.load(open(keymap.model_keymap_path("Test")))
    assert written["secondary_functions"]["f7"] == "kbd_backlight"


def test_only_the_legends_of_mapped_keys_are_written(config_root, monkeypatch):
    from alienfx_ctl import hardware, keymap
    monkeypatch.setattr(hardware, "slug", lambda text="": "test-9")
    grid = layout.reference()
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


# ------------------------------------------------------------- navigation
#
# An 85-key run is long enough that spotting a mistake near the end should not
# mean arrowing back through four rows, and that losing track of what is left
# is easy.

def test_a_digit_jumps_to_the_start_of_that_row(shipped_keymap):
    grid = layout.Layout.from_keymap(shipped_keymap)
    assigned, _, _ = _run(grid, ["3", term.ENTER, "q"])
    first_of_row_three = grid.rows[2][0][0]
    assert list(assigned) == [first_of_row_three]


def test_jumping_to_row_one_returns_to_the_beginning(shipped_keymap):
    grid = layout.Layout.from_keymap(shipped_keymap)
    assigned, _, _ = _run(grid, [term.DOWN, term.DOWN, "1", term.ENTER, "q"])
    assert list(assigned) == [grid.rows[0][0][0]]


def test_a_row_that_does_not_exist_is_refused_not_ignored(grid):
    lines = []
    wizard.assign_leds(grid, lambda changes: None,
                       term.from_sequence(["9", "q"]), lines.append)
    assert any("no row 9" in line for line in lines)


def test_jumping_offers_a_fresh_guess_for_the_new_key(shipped_keymap):
    """Not the candidate left over from wherever the cursor was."""
    grid = layout.Layout.from_keymap(shipped_keymap)
    assigned, probed, _ = _run(grid, [term.ENTER, "4", term.ENTER, "q"])
    values = sorted(assigned.values())
    assert values == [0, 1], f"the second key guessed the next index: {assigned}"


def test_review_reports_progress_for_every_row(shipped_keymap):
    grid = layout.Layout.from_keymap(shipped_keymap)
    lines = []
    wizard.assign_leds(grid, lambda changes: None,
                       term.from_sequence([term.ENTER, "r", "q"]), lines.append)
    review = [line for line in lines if line.strip().startswith("row ")]
    assert len(review) == len(grid.rows)
    assert "1/16" in review[0], review[0]


def test_review_names_what_is_still_missing(grid):
    lines = []
    wizard.assign_leds(grid, lambda changes: None,
                       term.from_sequence([term.ENTER, "r", "q"]), lines.append)
    text = "\n".join(lines)
    assert "still to do" in text


def test_review_does_not_move_the_light_or_assign_anything(grid):
    assigned, probed, _ = _run(grid, ["r", "r", term.ENTER, "q"])
    assert assigned == {"esc": 0}
    assert probed.count(0) == 1, "review is free"


def test_a_fully_mapped_row_reads_as_done(grid):
    lines = []
    keys = [term.ENTER] * len(grid.rows[0]) + ["1", "r", "q"]
    wizard.assign_leds(grid, lambda changes: None,
                       term.from_sequence(keys), lines.append)
    assert any("done" in line for line in lines if line.strip().startswith("row 1"))
