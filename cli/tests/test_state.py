"""Live state, profile names and atomic writes."""

import json
import os

import pytest

from alienfx_ctl import state


@pytest.mark.parametrize("name", ["Default", "a", "A1", "my-profile", "my_profile", "x" * 64])
def test_valid_profile_names(name):
    assert state.validate_name(name) == name


@pytest.mark.parametrize("name", [
    "", "   ", ".", "..", ".hidden", "-lead", "_lead",
    "has space", "has/slash", "has\\slash", "name.toml", "name.",
    "../escape", "/abs", "x" * 65, "emoji-✨",
])
def test_rejected_profile_names(name):
    """Names become filenames, so traversal and dotfiles must be refused."""
    with pytest.raises(state.StateError):
        state.validate_name(name)


def test_profile_path_stays_inside_the_profiles_dir(config_root):
    path = state.profile_path("Default")
    assert os.path.realpath(os.path.dirname(path)) == os.path.realpath(state.profiles_dir())


def test_traversal_via_profile_path_is_refused(config_root):
    with pytest.raises(state.StateError):
        state.profile_path("../../etc/passwd")


def test_defaults_when_nothing_is_saved(config_root):
    loaded = state.load_state()
    assert loaded["effect"] == "gradient"
    assert loaded["zonesync"] is True
    assert loaded["brightness"] == state.DEFAULT_BRIGHTNESS
    # ThemeSync is flag-derived, and no flag has been written yet.
    assert loaded["themesync"] is False


def test_save_then_load_round_trips(config_root):
    st = state.load_state()
    st["brightness"] = 99
    st["zones"]["kbd"]["color"] = "abcdef"
    state.save_state(st)
    assert state.load_state()["brightness"] == 99
    assert state.load_state()["zones"]["kbd"]["color"] == "abcdef"


def test_missing_keys_are_filled_from_defaults(config_root):
    """An older or hand-edited state file must keep working after an upgrade
    adds a field, rather than raising on the first missing key."""
    state.write_json_atomic(state.state_path(), {"brightness": 42})
    loaded = state.load_state()
    assert loaded["brightness"] == 42
    assert loaded["effect"] == "gradient"
    assert "kbd" in loaded["zones"]


def test_corrupt_state_falls_back_to_defaults(config_root):
    with open(state.state_path(), "w", encoding="utf-8") as fh:
        fh.write("{not json at all")
    assert state.load_state()["effect"] == "gradient"


def test_themesync_flag_is_the_source_of_truth(config_root):
    state.set_themesync(True)
    assert state.themesync_enabled() is True
    assert state.load_state()["themesync"] is True
    state.set_themesync(False)
    assert state.load_state()["themesync"] is False


def test_themesync_is_never_persisted_into_state(config_root):
    """Two sources of truth would let the hook and the CLI disagree."""
    state.set_themesync(True)
    state.save_state(state.load_state())
    on_disk = json.load(open(state.state_path(), encoding="utf-8"))
    assert "themesync" not in on_disk


def test_profile_save_load_list(config_root):
    st = state.load_state()
    st["brightness"] = 77
    state.save_profile("Night", st)
    assert state.list_profiles() == ["Night"]
    assert state.load_profile("Night")["brightness"] == 77


def test_profile_rename_moves_the_marker(config_root):
    state.save_profile("Old", state.load_state())
    state.set_current_profile("Old")
    state.rename_profile("Old", "New")
    assert state.list_profiles() == ["New"]
    assert state.current_profile() == "New"


def test_loading_a_missing_profile_is_an_error(config_root):
    with pytest.raises(state.StateError):
        state.load_profile("Nope")


def test_current_profile_marker_is_revalidated_on_read(config_root):
    """A tampered marker must report 'no profile', never build a path."""
    with open(state.current_profile_marker(), "w", encoding="utf-8") as fh:
        fh.write("../../etc/passwd\n")
    assert state.current_profile() is None


def test_atomic_write_leaves_no_temp_files(config_root):
    state.write_json_atomic(state.state_path(), {"a": 1})
    leftovers = [n for n in os.listdir(state.config_root()) if n.startswith(".tmp-")]
    assert leftovers == []


def test_atomic_write_preserves_the_old_file_on_failure(config_root):
    state.write_json_atomic(state.state_path(), {"good": True})

    class Unserialisable:
        pass

    with pytest.raises(TypeError):
        state.write_json_atomic(state.state_path(), {"bad": Unserialisable()})
    # The previous content must survive a failed write.
    assert json.load(open(state.state_path(), encoding="utf-8")) == {"good": True}
    assert [n for n in os.listdir(state.config_root()) if n.startswith(".tmp-")] == []
