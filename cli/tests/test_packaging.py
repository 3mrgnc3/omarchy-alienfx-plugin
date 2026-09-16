"""The plugin as a shipped artefact.

These used to be a GitHub Actions workflow. They are plain tests now, because
this project runs its checks locally and nothing about them needed a remote
runner - they only read files in the repository.

What they guard is the class of mistake that produces no error message at all:
a manifest Omarchy quietly refuses to load, a setting declared but never read, a
bundled layout with no label. You find out by the plugin simply not appearing.
"""

import json
import os
import re
import subprocess

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _read(*parts):
    with open(os.path.join(REPO, *parts), encoding="utf-8") as handle:
        return handle.read()


@pytest.fixture(scope="module")
def manifest():
    return json.loads(_read("manifest.json"))


# ---------------------------------------------------------------- manifest

def test_the_manifest_has_what_the_registry_requires(manifest):
    """PluginRegistry.validateManifest rejects the whole manifest if any of
    these are missing or schemaVersion is not exactly 1, and the only symptom is
    a plugin that never loads."""
    assert manifest.get("schemaVersion") == 1
    for key in ("id", "name", "version", "kinds", "entryPoints"):
        assert key in manifest, f"missing required field: {key}"
    assert isinstance(manifest["kinds"], list) and manifest["kinds"]
    assert isinstance(manifest["entryPoints"], dict)


def test_the_plugin_id_follows_the_registry_rules(manifest):
    plugin_id = str(manifest["id"])
    assert plugin_id
    assert "/" not in plugin_id and ".." not in plugin_id
    assert not plugin_id.startswith("/")
    # The registry itself only forbids "/", ".." and a leading "/". One dot is
    # the <author>.<name> convention, not a rule - another installed plugin
    # here is io.github.0x1ocean.server-mode, with three - so require at least
    # one rather than exactly one.
    assert "." in plugin_id, "id should be <author>.<name>"
    assert not plugin_id.startswith(".") and not plugin_id.endswith(".")


def test_entry_points_exist_and_cannot_escape_the_plugin_folder(manifest):
    for kind, entry in manifest["entryPoints"].items():
        assert not os.path.isabs(entry), f"{kind} must be relative"
        assert ".." not in entry, f"{kind} must not contain .."
        assert os.path.isfile(os.path.join(REPO, entry)), f"{kind} -> {entry} missing"


def test_the_manifest_carries_what_a_reviewer_expects(manifest):
    for key in ("author", "license", "description", "version"):
        assert manifest.get(key), f"should declare {key}"


def test_the_bar_section_is_one_the_shell_knows(manifest):
    section = (manifest.get("barWidget") or {}).get("defaultSection")
    if section is not None:
        assert section in ("left", "center", "right")


def test_the_cli_and_the_manifest_agree_on_the_version(manifest):
    """They surface in different places - the plugin listing and
    `alienfx-ctl --version` - so a mismatch makes bug reports ambiguous."""
    from alienfx_ctl import __version__
    assert manifest["version"] == __version__


# ---------------------------------------------------------------- settings

def test_declared_settings_match_the_settings_the_qml_reads(manifest):
    """Both directions matter. A setting the QML reads but the manifest never
    declares cannot be configured; one declared but never read is a lie in the
    settings form. Neither produces an error."""
    widget = manifest.get("barWidget") or {}
    declared = {field["key"] for field in widget.get("schema", [])}
    defaults = set(widget.get("defaults") or {})
    read = set(re.findall(r'setting\(\s*"([^"]+)"', _read("Panel.qml")))

    assert not (read - declared), f"read but not declared: {sorted(read - declared)}"
    assert not (declared - read), f"declared but never read: {sorted(declared - read)}"
    assert declared == defaults, f"schema and defaults disagree: {sorted(declared ^ defaults)}"


# ----------------------------------------------------------------- layouts

def test_every_bundled_shape_extension_is_described_honestly():
    from alienfx_ctl import layout
    raw = json.loads(_read("cli", "src", "alienfx_ctl", "data", "layout-extensions.json"))
    assert raw["extensions"]
    for name, spec in raw["extensions"].items():
        assert spec.get("label"), f"{name}: no label to show the user"
        assert spec.get("source"), f"{name}: does not say where it came from"
        assert spec.get("verified") is False, f"{name}: claims to be verified"
        assert spec.get("keys"), f"{name}: adds nothing"


def test_no_bundled_shape_carries_an_led_index_or_restates_the_keymap():
    """Indices are irregular on real hardware and cannot be inferred - a wrong
    one silently lights the wrong key. And anything the shipped keymap already
    describes must not be stored a second time."""
    from alienfx_ctl import layout
    raw = json.loads(_read("cli", "src", "alienfx_ctl", "data", "layout-extensions.json"))
    base = set(layout.reference().keys())
    for name, spec in raw["extensions"].items():
        seen = set()
        for entry in spec["keys"]:
            assert len(entry) == 3, f"{name}: unexpected field in {entry}"
            key, row, col = entry
            assert isinstance(key, str) and key
            assert isinstance(row, int) and row >= 0
            assert isinstance(col, int) and col >= 0
            assert key not in seen, f"{name}: {key} appears twice"
            assert key not in base, f"{name}: {key} is already in the shipped keymap"
            seen.add(key)


def test_the_shipped_keymap_is_valid():
    from alienfx_ctl import keymap
    keymap.validate(json.loads(_read("cli", "src", "alienfx_ctl", "data", "m16r2-keymap.json")))


# ------------------------------------------------------------- the tree

def test_the_plugin_folder_contains_no_symlinks():
    """Omarchy refuses them inside a plugin folder."""
    found = subprocess.run(
        ["find", ".", "-type", "l", "-not", "-path", "./.git/*"],
        cwd=REPO, capture_output=True, text=True).stdout.strip()
    assert not found, f"symlinks found:\n{found}"


def test_every_shipped_shell_script_parses():
    scripts = ["install.sh", "uninstall.sh",
               "share/bin/omarchy-alienfx-wizard",
               "share/omarchy/hooks/theme-set.d/50-omarchy-alienfx"]
    for script in scripts:
        result = subprocess.run(["bash", "-n", os.path.join(REPO, script)],
                                capture_output=True, text=True)
        assert result.returncode == 0, f"{script}: {result.stderr}"


def test_the_whole_cli_byte_compiles():
    result = subprocess.run(
        ["python3", "-m", "compileall", "-q", os.path.join(REPO, "cli", "src", "alienfx_ctl")],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_readme_test_count_is_current():
    """A stale count quietly tells a reader the docs are not maintained."""
    out = subprocess.run(["python3", "-m", "pytest", "tests", "-q", "--collect-only"],
                         cwd=os.path.join(REPO, "cli"),
                         capture_output=True, text=True).stdout
    found = re.search(r"(\d+) tests? collected", out)
    assert found, f"could not read a test count from pytest:\n{out[-400:]}"
    claimed = {int(n) for n in re.findall(r"(\d+) tests?, no hardware", _read("README.md"))}
    assert claimed == {int(found.group(1))}, \
        f"README claims {claimed}, actual {found.group(1)}"


# ------------------------------------------------- removing from the CLI
#
# `omarchy plugin remove` deletes the plugin folder, which is where
# uninstall.sh lives. Doing it in that order strands the CLI, the udev rule,
# both user services and the theme hook with no obvious way left to remove
# them. `alienfx-ctl uninstall` works afterwards.

def test_the_installer_keeps_a_copy_of_the_uninstaller_outside_the_plugin():
    """The whole point: it has to outlive the folder being deleted."""
    install = _read("install.sh")
    assert '"$SHARE_DIR/uninstall.sh"' in install


def test_the_cli_runs_the_uninstaller_from_a_copy_not_in_place():
    """bash reads a script as it goes, and this one deletes the directory it
    sits in. Running it in place risks it being read out from under itself."""
    import inspect
    from alienfx_ctl import cli
    source = inspect.getsource(cli.cmd_uninstall)
    assert "mkstemp" in source
    assert "shutil.copy2" in source


def test_the_cli_looks_beside_its_own_package_first():
    """Derived from where the module actually sits, so it is right for a
    checkout as well as an install."""
    import inspect
    from alienfx_ctl import cli
    source = inspect.getsource(cli.cmd_uninstall)
    assert "__file__" in source


def test_a_missing_uninstaller_is_explained_rather_than_silently_ignored(monkeypatch, tmp_path):
    from alienfx_ctl import cli
    monkeypatch.setattr(os.path, "isfile", lambda p: False)
    code = cli.cmd_uninstall(type("A", (), {"purge": False, "yes": False})())
    assert code != 0


def test_the_uninstaller_refuses_when_it_cannot_ask():
    """It used to remove everything silently when run without a terminal, which
    is how a script or an accidental pipe could wipe the install with no
    confirmation at all."""
    script = _read("uninstall.sh")
    assert "not interactive and --yes was not given" in script
    assert "nothing was changed" in script


def test_the_uninstaller_states_what_it_will_delete_before_asking():
    script = _read("uninstall.sh")
    for expected in ("This will REMOVE", "Are you sure",
                     "needs your password", "The lights are turned off"):
        assert expected in script, expected


def test_the_uninstaller_is_explicit_about_user_data_either_way():
    """Losing a probed keymap costs the user several minutes to redo, so both
    the keep and the delete path say so plainly."""
    script = _read("uninstall.sh")
    assert "ALSO DELETED, because you passed --purge" in script
    assert "KEPT:" in script
