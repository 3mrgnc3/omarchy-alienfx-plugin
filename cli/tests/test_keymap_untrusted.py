"""Keymaps are untrusted input.

The contribution flow invites people to send each other these files, so one may
be hostile or simply corrupt. Every test here is a thing that was accepted
before the validator was hardened, checked against the hardened one.

Two bounds are about safety rather than correctness and are worth stating:

* Names and free text are printed into the wizard's terminal *while it is in raw
  mode*. A value carrying escape bytes can retitle the window, clear the screen,
  or hide text in the very display the user is reading to map their keys - and
  the wizard is exactly where a shared keymap gets opened.
* `layout.render` indents each key by its column, so an unbounded column is a
  memory-exhaustion primitive: col=10**9 asked for a six-gigabyte line.
"""

import json

import pytest

from alienfx_ctl import keymap, layout, term, wizard

ESCAPE = "\x1b]0;HIJACKED\x07\x1b[2J"


def _base():
    return {"key_to_index": {"esc": 0},
            "grid_positions": {"esc": {"row": 0, "col": 0}}}


def _rejected(data):
    with pytest.raises(keymap.KeymapError):
        keymap.validate(data)


# ------------------------------------------------------- terminal injection

def test_escape_bytes_in_a_key_name_are_refused():
    _rejected({"key_to_index": {ESCAPE: 0},
               "grid_positions": {ESCAPE: {"row": 0, "col": 0}}})


def test_escape_bytes_in_an_fn_legend_are_refused():
    _rejected(dict(_base(), secondary_functions={"esc": "\x1b[31mfake\x1b[0m"}))


def test_escape_bytes_in_the_model_name_are_refused():
    """`keymap show` prints this straight to the terminal."""
    _rejected(dict(_base(), device=ESCAPE))


def test_a_newline_in_a_name_is_refused():
    """Newlines would let one value forge extra lines of wizard output."""
    _rejected({"key_to_index": {"esc\nf1": 0},
               "grid_positions": {"esc\nf1": {"row": 0, "col": 0}}})


def test_nothing_a_validated_keymap_prints_can_carry_an_escape():
    """The end-to-end property: drive the wizard with a validated keymap and
    assert no escape byte reaches the output."""
    with open(keymap.SHIPPED_KEYMAP, encoding="utf-8") as handle:
        grid = layout.Layout.from_keymap(keymap.validate(json.load(handle)))
    lines = []
    wizard.assign_leds(grid, lambda changes: None,
                       term.from_sequence([term.DOWN] * 20 + ["q"]), lines.append)
    assert lines
    assert not any("\x1b" in line for line in lines)


def test_an_error_message_cannot_carry_an_escape_either():
    """The rejection path prints the offending value, so it has to escape it -
    otherwise refusing the file would itself run the attack."""
    with pytest.raises(keymap.KeymapError) as caught:
        keymap.validate({"key_to_index": {ESCAPE: 0},
                         "grid_positions": {ESCAPE: {"row": 0, "col": 0}}})
    assert "\x1b" not in str(caught.value)


def test_an_error_message_does_not_echo_a_huge_value():
    with pytest.raises(keymap.KeymapError) as caught:
        keymap.validate({"key_to_index": {"a" * 100_000: 0},
                         "grid_positions": {"a" * 100_000: {"row": 0, "col": 0}}})
    assert len(str(caught.value)) < 300


# ------------------------------------------------------ resource exhaustion

def test_an_unbounded_column_is_refused():
    _rejected({"key_to_index": {"esc": 0},
               "grid_positions": {"esc": {"row": 0, "col": 10 ** 9}}})


def test_an_unbounded_row_is_refused():
    _rejected({"key_to_index": {"esc": 0},
               "grid_positions": {"esc": {"row": 10 ** 9, "col": 0}}})


def test_too_many_keys_is_refused():
    many = {f"k{i}": i % 200 for i in range(keymap.MAX_KEYS + 1)}
    _rejected({"key_to_index": many,
               "grid_positions": {name: {"row": 0, "col": 0} for name in many}})


def test_an_overlong_name_is_refused():
    long_name = "a" * (keymap.MAX_NAME_LENGTH + 1)
    _rejected({"key_to_index": {long_name: 0},
               "grid_positions": {long_name: {"row": 0, "col": 0}}})


def test_an_oversized_file_is_refused_before_it_is_parsed(tmp_path):
    """Parsing is what allocates, so the size check has to come first."""
    path = tmp_path / "huge-keymap.json"
    path.write_text("[" + "0," * (keymap.MAX_FILE_BYTES // 2) + "0]")
    with pytest.raises(keymap.KeymapError) as caught:
        keymap.load_file(str(path))
    assert "larger than" in str(caught.value)


def test_a_column_that_passes_validation_cannot_blow_up_the_renderer():
    """The bound exists to protect this. At the limit the drawn line stays
    small; without it the same code path allocated gigabytes."""
    data = {"key_to_index": {"esc": 0, "far": 1},
            "grid_positions": {"esc": {"row": 0, "col": 0},
                               "far": {"row": 0, "col": keymap.MAX_GRID_COL}}}
    keymap.validate(data)
    drawn = layout.Layout.from_keymap(data).render(cursor=(0, 0))
    assert len(drawn) < 1000


# ------------------------------------------------------------ type confusion

def test_a_non_string_key_is_refused():
    _rejected({"key_to_index": {"esc": 0, 5: 1},
               "grid_positions": {"esc": {"row": 0, "col": 0}}})


@pytest.mark.parametrize("bad", [True, False])
def test_a_boolean_led_index_is_refused(bad):
    """bool is an int in Python, so True would sail through a range check."""
    _rejected({"key_to_index": {"esc": bad},
               "grid_positions": {"esc": {"row": 0, "col": 0}}})


@pytest.mark.parametrize("bad", ["0", 1.5, None, [], {}])
def test_a_non_integer_led_index_is_refused(bad):
    _rejected({"key_to_index": {"esc": bad},
               "grid_positions": {"esc": {"row": 0, "col": 0}}})


def test_an_led_index_outside_the_strip_is_refused():
    _rejected({"key_to_index": {"esc": 10 ** 6},
               "grid_positions": {"esc": {"row": 0, "col": 0}}})


def test_malformed_json_is_an_error_not_a_traceback(tmp_path):
    path = tmp_path / "broken-keymap.json"
    path.write_text("{ not json")
    with pytest.raises(keymap.KeymapError):
        keymap.load_file(str(path))


def test_a_json_array_is_refused():
    _rejected([1, 2, 3])


# ------------------------------------------------------------- chassis zones

@pytest.mark.parametrize("bad", [99999, -5, 256, True, "2", 1.5, None])
def test_a_light_id_outside_a_byte_is_refused(bad):
    """Each id becomes one byte of a HID packet. An out-of-range id used to
    reach bytes() and raise ValueError from inside the writer."""
    _rejected({"keyboard": "zones", "zones": {"kb": [bad]}})


def test_too_many_light_ids_in_a_zone_is_refused():
    _rejected({"keyboard": "zones",
               "zones": {"kb": list(range(keymap.MAX_IDS_PER_ZONE + 1))}})


def test_too_many_zones_is_refused():
    _rejected({"keyboard": "zones",
               "zones": {f"z{i}": [1] for i in range(keymap.MAX_ZONES + 1)}})


def test_a_zone_name_cannot_look_like_a_path():
    """Zone names are not used to build paths today, and this keeps it that way
    if one ever is."""
    _rejected({"keyboard": "zones", "zones": {"../../etc/passwd": [1]}})


def test_an_empty_zone_is_refused():
    _rejected({"keyboard": "zones", "zones": {"kb": []}})


def test_an_unknown_keyboard_kind_is_refused():
    _rejected({"keyboard": "telepathy", "zones": {"kb": [1]}})


# ------------------------------------------------------------- still usable

def test_the_shipped_keymap_passes_every_check():
    """The bounds are meant to be generous against real data: 85 keys, names of
    at most 10 lowercase characters, grid reaching row 5 column 16."""
    with open(keymap.SHIPPED_KEYMAP, encoding="utf-8") as handle:
        keymap.validate(json.load(handle))


def test_a_realistically_large_keyboard_still_fits():
    """A keypad, a macro column and a full function row, comfortably inside."""
    names = [f"k{i}" for i in range(180)]
    data = {"key_to_index": {name: i for i, name in enumerate(names)},
            "grid_positions": {name: {"row": i // 24, "col": i % 24}
                               for i, name in enumerate(names)}}
    keymap.validate(data)


def test_legends_may_contain_the_punctuation_real_ones_use():
    """Real legends look like `` ` ~ `` and "volume_up" - printable, not plain."""
    keymap.validate(dict(_base(), secondary_functions={"esc": "`  ~ (F1) {|}"}))
