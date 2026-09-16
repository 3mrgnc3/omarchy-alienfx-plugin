"""Keymap validation and import."""

import json
import os

import pytest

from alienfx_ctl import hardware, keymap, state


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


# ------------------------------------------- keyboards lit as chassis zones
#
# Most older Alienware laptops light the keyboard as four APIv4 zones and have
# no APIv5 controller at all. Deliberately not a special case: such a machine is
# one with more chassis zones and no keyboard, so nothing downstream branches on
# it - device.zone_names() simply omits "kbd", and gradient.elc_samples already
# spreads unfamiliar zone names along the blend axis.

ZONED = {
    "device": "Alienware m15 R1",
    "keyboard": "zones",
    "zones": {"kb1": [2], "kb2": [3], "kb3": [4], "kb4": [5],
              "logo": [1], "pbtn": [0]},
}


def test_a_zone_only_keymap_validates_without_per_key_data():
    assert keymap.validate(dict(ZONED)) == ZONED


def test_a_zone_only_keymap_still_needs_some_zones():
    """Otherwise it describes nothing at all."""
    with pytest.raises(keymap.KeymapError):
        keymap.validate({"keyboard": "zones", "zones": {}})
    with pytest.raises(keymap.KeymapError):
        keymap.validate({"keyboard": "zones"})


def test_a_per_key_keymap_still_requires_per_key_data():
    """The default must not have been loosened by adding the other kind."""
    with pytest.raises(keymap.KeymapError):
        keymap.validate({"zones": {"logo": [1]}})


def test_an_unknown_keyboard_kind_is_refused():
    with pytest.raises(keymap.KeymapError):
        keymap.validate({"keyboard": "telepathy", "zones": {"logo": [1]}})


def test_a_keymap_with_no_keyboard_field_is_per_key(shipped_keymap):
    """Every keymap written before this field existed is per-key."""
    assert "keyboard" not in shipped_keymap
    assert keymap.has_per_key_keyboard(shipped_keymap) is True
    assert keymap.keyboard_kind(shipped_keymap) == keymap.KEYBOARD_PER_KEY


def test_a_zone_only_keymap_reports_itself_as_such():
    assert keymap.has_per_key_keyboard(ZONED) is False
    assert keymap.keyboard_kind(ZONED) == keymap.KEYBOARD_ZONES


def test_the_zone_order_is_preserved_because_it_sets_the_blend_order():
    """With no per-key data, the order zones are declared in is the only
    control anyone has over how the gradient travels through them."""
    assert list(keymap.zones(ZONED)) == ["kb1", "kb2", "kb3", "kb4", "logo", "pbtn"]


def test_such_a_machine_has_no_kbd_zone(config_root, monkeypatch):
    """Including one would make every apply try to open an APIv5 controller the
    machine does not contain."""
    from alienfx_ctl import device
    monkeypatch.setattr(keymap, "load", lambda: dict(ZONED))
    names = device.zone_names()
    assert "kbd" not in names
    assert set(names) == set(ZONED["zones"])


def test_such_a_machine_gets_a_gradient_across_its_keyboard_zones(config_root, monkeypatch):
    """The whole point: no code change, just data."""
    from alienfx_ctl import device, engine, state
    monkeypatch.setattr(keymap, "load", lambda: dict(ZONED))
    st = dict(state.DEFAULT_STATE)
    st.update(brightness=255, intensity=0, effect="gradient")
    plan = engine.plan(st, list(device.zone_names()))
    assert plan["kbd_leds"] is None, "there is no per-key keyboard to render"
    assert set(plan["elc"]) == set(ZONED["zones"])
    assert len(set(plan["elc"].values())) >= 4, "a real blend, not a flat fill"


def test_the_wizard_can_write_one(config_root, monkeypatch):
    """A machine with no per-key controller can still be described."""
    import json
    from alienfx_ctl import hardware, wizard
    monkeypatch.setattr(hardware, "slug", lambda text="": "m15-r1")
    assert wizard._save_zoned("Alienware m15 R1", ZONED["zones"]) == 0
    written = json.load(open(keymap.model_keymap_path("Alienware m15 R1")))
    keymap.validate(written)
    assert written["keyboard"] == "zones"
    assert list(written["zones"]) == list(ZONED["zones"])


# ------------------------------------------------- keymaps contributed by users
#
# Supporting another model is meant to be a data change with no code behind it:
# drop alienware-<slug>-keymap.json beside the reference map and an owner of
# that machine gets it automatically.

def test_a_contributed_keymap_for_this_model_is_preferred_over_the_reference(
        config_root, tmp_path, monkeypatch, shipped_keymap):
    """Without this the contributed file would sit in the package unused while
    its owner silently got the m16 R2 map - wrong indices, no error."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    reference = data_dir / "m16r2-keymap.json"
    reference.write_text(json.dumps(shipped_keymap))
    monkeypatch.setattr(keymap, "SHIPPED_KEYMAP", str(reference))
    monkeypatch.setattr(keymap.hardware, "model", lambda: "Alienware m15 R3")
    monkeypatch.setattr(keymap.hardware, "keymap_filename",
                        lambda text="": "alienware-m15-r3-keymap.json")

    assert keymap.load()["device"] == shipped_keymap["device"], "reference first"

    mine = dict(shipped_keymap, device="Alienware m15 R3")
    (data_dir / "alienware-m15-r3-keymap.json").write_text(json.dumps(mine))
    assert keymap.load()["device"] == "Alienware m15 R3"


def test_the_user_s_own_keymap_still_wins_over_a_contributed_one(
        config_root, tmp_path, monkeypatch, shipped_keymap):
    """Someone who probed their own machine must not be overridden by a map
    contributed for the same model."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "m16r2-keymap.json").write_text(json.dumps(shipped_keymap))
    monkeypatch.setattr(keymap, "SHIPPED_KEYMAP", str(data_dir / "m16r2-keymap.json"))
    contributed = dict(shipped_keymap, device="contributed")
    (data_dir / hardware.keymap_filename()).write_text(json.dumps(contributed))

    mine = dict(shipped_keymap, device="mine")
    state.write_json_atomic(keymap.user_keymap_path(), mine)
    assert keymap.load()["device"] == "mine"


def test_every_bundled_keymap_is_valid():
    """A contributed map that does not validate would break the machine it was
    meant to help, so this runs over whatever is in the package."""
    import glob
    directory = os.path.dirname(keymap.SHIPPED_KEYMAP)
    for path in glob.glob(os.path.join(directory, "*keymap*.json")):
        keymap.validate(keymap.load_file(path))


# ------------------------------- is this machine mapped, or does it need the wizard
#
# The popup's large first-run wizard prompt used to key off has_user_keymap,
# which asks whether the user probed a map themselves. That is the wrong
# question: an owner of the reference model, or of any model someone has
# contributed a map for, is already mapped correctly and has nothing to do. The
# prompt appeared anyway, next to a keyboard that was lighting perfectly.

def test_a_probed_keymap_counts(config_root, shipped_keymap):
    state.write_json_atomic(keymap.user_keymap_path(), shipped_keymap)
    assert keymap.has_keymap_for_this_machine() is True


def test_the_reference_model_is_mapped_without_probing_anything(config_root, monkeypatch):
    """This machine is the one the shipped keymap describes, so the fallback is
    correct and the wizard prompt should stay hidden."""
    monkeypatch.setattr(hardware, "model", lambda: "Alienware m16 R2")
    assert keymap.has_user_keymap() is False
    assert keymap.has_keymap_for_this_machine() is True


def test_a_different_model_is_not_mapped_by_the_reference(config_root, monkeypatch):
    """The reference map would light the wrong keys here, so the wizard prompt
    is exactly what this user needs."""
    monkeypatch.setattr(hardware, "model", lambda: "Alienware x17 R2")
    monkeypatch.setattr(hardware, "slug", lambda text="": "x17-r2" if not text else
                        ("m16-r2" if "m16" in text else "x17-r2"))
    assert keymap.has_keymap_for_this_machine() is False


def test_a_contributed_keymap_for_this_model_counts(config_root, tmp_path, monkeypatch,
                                                    shipped_keymap):
    """Someone else probed this model and it ships with the plugin, so the owner
    has nothing to do."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    reference = data_dir / "m16r2-keymap.json"
    reference.write_text(json.dumps(shipped_keymap))
    monkeypatch.setattr(keymap, "SHIPPED_KEYMAP", str(reference))
    monkeypatch.setattr(hardware, "model", lambda: "Alienware m15 R3")
    monkeypatch.setattr(hardware, "keymap_filename",
                        lambda text="": "alienware-m15-r3-keymap.json")
    # Faithful: slug("") is this machine, slug(<name>) slugs that name. A mock
    # returning one value for both made the reference-model check match itself.
    monkeypatch.setattr(hardware, "slug",
                        lambda text="": "m15-r3" if not text else
                        "m16-r2" if "m16" in text.lower() else "m15-r3")

    assert keymap.has_keymap_for_this_machine() is False, "nothing for this model yet"
    (data_dir / "alienware-m15-r3-keymap.json").write_text(json.dumps(shipped_keymap))
    assert keymap.has_keymap_for_this_machine() is True


def test_an_unreadable_reference_does_not_claim_the_machine_is_mapped(config_root, monkeypatch):
    monkeypatch.setattr(keymap, "SHIPPED_KEYMAP", "/nonexistent/keymap.json")
    assert keymap.has_keymap_for_this_machine() is False


def test_the_state_payload_reports_it(config_root, monkeypatch):
    """This is the field the popup reads to decide whether to show the prompt."""
    from alienfx_ctl import cli
    monkeypatch.setattr(keymap, "has_keymap_for_this_machine", lambda: True)
    monkeypatch.setattr(keymap, "has_user_keymap", lambda: False)
    import io, contextlib
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        cli.cmd_state(type("A", (), {"json": True, "verbose": False})())
    assert json.loads(out.getvalue())["has_keymap"] is True
