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
