"""Keymap validation and import."""

import json

import pytest

from alienfx_ctl import keymap


def good():
    return {
        "device": "Test",
        "key_to_index": {"esc": 0, "a": 1},
        "grid_positions": {"esc": {"row": 0, "col": 0}, "a": {"row": 1, "col": 0}},
    }


def test_valid_keymap_passes():
    assert keymap.validate(good()) is not None


def test_the_shipped_keymap_is_valid():
    """The fallback map must always load, or a fresh install cannot render."""
    data = keymap.load_file(keymap.SHIPPED_KEYMAP)
    assert data["key_to_index"]
    assert data["grid_positions"]


@pytest.mark.parametrize("mutate", [
    lambda d: d.pop("key_to_index"),
    lambda d: d.pop("grid_positions"),
    lambda d: d.update(key_to_index={}),
    lambda d: d.update(key_to_index={"a": "one"}),
    lambda d: d.update(key_to_index={"a": -1}),
    lambda d: d.update(key_to_index={"a": 500}),
    lambda d: d.update(key_to_index={"a": True}),
    lambda d: d.update(grid_positions={"a": {"row": 0}}),
    lambda d: d.update(grid_positions={"a": {"row": -1, "col": 0}}),
    lambda d: d.update(grid_positions={"a": "nope"}),
    lambda d: d.update(grid_positions={"a": [1, 2, 3]}),
])
def test_malformed_keymaps_are_rejected(mutate):
    """Rejected rather than repaired: a bad index paints the wrong keys, which
    is more confusing than a clear error."""
    data = good()
    mutate(data)
    with pytest.raises(keymap.KeymapError):
        keymap.validate(data)


def test_non_object_is_rejected():
    with pytest.raises(keymap.KeymapError):
        keymap.validate([1, 2, 3])


def test_import_installs_the_user_keymap(config_root, tmp_path):
    source = tmp_path / "mine.json"
    source.write_text(json.dumps(good()), encoding="utf-8")

    assert keymap.has_user_keymap() is False
    target = keymap.import_file(str(source))
    assert keymap.has_user_keymap() is True
    assert json.load(open(target, encoding="utf-8"))["device"] == "Test"


def test_import_rejects_a_bad_file_without_installing(config_root, tmp_path):
    source = tmp_path / "bad.json"
    source.write_text('{"key_to_index": {}}', encoding="utf-8")
    with pytest.raises(keymap.KeymapError):
        keymap.import_file(str(source))
    assert keymap.has_user_keymap() is False


def test_import_rejects_invalid_json(config_root, tmp_path):
    source = tmp_path / "bad.json"
    source.write_text("{oh no", encoding="utf-8")
    with pytest.raises(keymap.KeymapError):
        keymap.import_file(str(source))


def test_user_keymap_wins_over_the_shipped_one(config_root, tmp_path):
    source = tmp_path / "mine.json"
    source.write_text(json.dumps(good()), encoding="utf-8")
    keymap.import_file(str(source))
    assert keymap.load()["device"] == "Test"


def test_load_falls_back_to_shipped(config_root):
    assert keymap.has_user_keymap() is False
    assert keymap.load()["key_to_index"]


def test_led_count_is_the_keymaps_extent():
    """Both paint paths derive their range from this, so it has to be exact."""
    assert keymap.led_count({"key_to_index": {"a": 0, "b": 12}}) == 13
    assert keymap.led_count({"key_to_index": {"a": 5}}) == 6


def test_led_count_of_the_real_keymap():
    data = keymap.load_file(keymap.SHIPPED_KEYMAP)
    # highest named index on the reference keyboard is 159
    assert keymap.led_count(data) == 160


def test_led_count_is_clamped_to_the_protocol_ceiling():
    from alienfx_ctl.apiv5 import KBD_LED_COUNT
    assert keymap.led_count({"key_to_index": {"a": 99999}}) == KBD_LED_COUNT


def test_led_count_falls_back_when_nothing_is_mapped():
    from alienfx_ctl.apiv5 import KBD_LED_COUNT
    assert keymap.led_count({}) == KBD_LED_COUNT


# ------------------------------------------------- indices confirmed on hardware

def test_backslash_is_index_55_not_54():
    """Pinned because it was wrong, and wrongness here is invisible in code.

    Confirmed by probe on the reference machine: lighting 54 and 55 alone showed
    green (55) under the `\\` / `|` keycap and no visible red (54) anywhere - so
    55 is the LED under that key and 54 drives nothing.

    The file shipped with 54 for a long time. The gradient paints only named
    indices, so it painted a dead position and left the real LED holding
    whatever the last full-range write put there - magenta after a `solid`,
    black after an `off`. The value came from an old wizard run into an
    untracked config file, so no commit recorded the change.
    """
    data = keymap.load_file(keymap.SHIPPED_KEYMAP)
    assert data["key_to_index"]["backslash"] == 55
    assert data["index_to_key"]["55"] == "backslash"
    assert "54" not in data["index_to_key"]


def test_index_to_key_is_an_exact_inverse():
    """The two tables are edited by hand and by the wizard; they drift silently
    if nothing checks them."""
    data = keymap.load_file(keymap.SHIPPED_KEYMAP)
    key_to_index = data["key_to_index"]
    index_to_key = data["index_to_key"]
    assert {str(v): k for k, v in key_to_index.items()} == index_to_key


def test_no_two_keys_share_an_led_index():
    """A duplicate means one of the two keys never gets its own colour."""
    data = keymap.load_file(keymap.SHIPPED_KEYMAP)
    indices = list(data["key_to_index"].values())
    assert len(set(indices)) == len(indices)


def test_every_named_key_has_a_grid_position():
    """A key with no position lands mid-blend rather than in its place, which
    reads as one key being the wrong colour."""
    data = keymap.load_file(keymap.SHIPPED_KEYMAP)
    assert set(data["key_to_index"]) == set(data["grid_positions"])
