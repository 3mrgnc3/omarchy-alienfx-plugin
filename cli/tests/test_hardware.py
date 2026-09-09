"""Identifying the machine.

Every per-model file this plugin writes is named from `slug()`, so a bad slug
means a keymap saved under a name nothing will ever look for - which presents
as "the wizard ran and changed nothing". This module had no tests and the whole
multi-model story rests on it.
"""

import os

import pytest

from alienfx_ctl import hardware, keymap


@pytest.fixture()
def dmi(tmp_path, monkeypatch):
    """A fake DMI tree, so tests do not depend on the machine they run on."""
    monkeypatch.setattr(hardware, "_DMI", str(tmp_path))

    def write(**fields):
        for name, value in fields.items():
            (tmp_path / name).write_text(value + "\n")
    return write


# ------------------------------------------------------------------- reading

def test_dmi_fields_are_read_and_stripped(dmi):
    dmi(sys_vendor="Alienware", product_name="Alienware m16 R2",
        product_sku="0C91", bios_version="1.19.0")
    assert hardware.vendor() == "Alienware"
    assert hardware.model() == "Alienware m16 R2"
    assert hardware.sku() == "0C91"
    assert hardware.bios_version() == "1.19.0"


def test_a_machine_with_no_dmi_does_not_crash(dmi):
    """Containers and VMs have no DMI. The plugin should report an unknown
    machine, not fail to import."""
    dmi()
    assert hardware.model() == "unknown"
    assert hardware.vendor() == ""
    assert hardware.slug() == "unknown"
    assert hardware.looks_supported() is False


# --------------------------------------------------------------------- slugs

@pytest.mark.parametrize("product,expected", [
    ("Alienware m16 R2", "m16-r2"),
    ("Alienware m15 R3", "m15-r3"),
    ("Alienware m17 R5", "m17-r5"),
    ("Alienware x15 R1", "x15-r1"),
    ("Alienware x17 R2", "x17-r2"),
    ("Alienware Area-51m R2", "area-51m-r2"),
    ("Alienware 17 R5", "17-r5"),
    ("Dell G15 5530", "g15-5530"),
    ("Dell G5 15 SE", "g5-15-se"),
])
def test_model_names_become_sensible_slugs(product, expected):
    """The vendor prefix is dropped because the filename already carries it,
    and a bare `m16-r2` is what a user would think to type."""
    assert hardware.slug(product) == expected


def test_slugs_are_case_and_whitespace_insensitive():
    assert hardware.slug("  ALIENWARE M16 R2  ") == "m16-r2"


def test_a_model_name_with_no_usable_characters_falls_back(dmi):
    dmi(product_name="???")
    assert hardware.slug("???") == "unknown"


def test_a_slug_can_never_escape_its_directory():
    """slug() feeds a filename, so a hostile or broken DMI string must not
    produce path separators. Firmware strings are not a trusted input."""
    for hostile in ("../../etc/passwd", "/etc/shadow", "..", "a/b/c",
                    "x\x00y", "..\\..\\windows"):
        result = hardware.slug(hostile)
        assert "/" not in result and "\\" not in result
        assert result not in ("", ".", "..")
        assert "\x00" not in result


def test_the_keymap_filename_is_built_from_the_slug():
    assert hardware.keymap_filename("Alienware m16 R2") == "alienware-m16-r2-keymap.json"


def test_the_keymap_filename_is_always_a_bare_name():
    """It is joined onto the config directory, so it must not contain a path."""
    for hostile in ("../../etc/passwd", "Dell G15 5530", "???"):
        name = hardware.keymap_filename(hostile)
        assert os.path.basename(name) == name
        assert name.endswith("-keymap.json")


def test_the_loader_and_the_wizard_agree_on_the_filename(config_root):
    """The bug this guards: if the wizard saves under one name and the loader
    looks for another, the wizard appears to run and change nothing."""
    saved = keymap.model_keymap_path(hardware.model())
    assert os.path.basename(saved) == hardware.keymap_filename()
    assert saved == keymap.user_keymap_path()


# ------------------------------------------------------------------ describe

def test_describe_does_not_repeat_the_vendor(dmi):
    """product_name is usually already "Alienware m16 R2"."""
    dmi(sys_vendor="Alienware", product_name="Alienware m16 R2")
    assert hardware.describe().count("Alienware") == 1


def test_describe_adds_the_vendor_when_it_is_missing(dmi):
    dmi(sys_vendor="Dell Inc.", product_name="G15 5530")
    assert hardware.describe().startswith("Dell Inc. G15 5530")


def test_describe_includes_sku_and_bios_when_present(dmi):
    dmi(sys_vendor="Alienware", product_name="Alienware m16 R2",
        product_sku="0C91", bios_version="1.19.0")
    described = hardware.describe()
    assert "sku 0C91" in described and "BIOS 1.19.0" in described


def test_describe_omits_the_bracket_when_there_is_nothing_to_put_in_it(dmi):
    dmi(sys_vendor="Alienware", product_name="Alienware m16 R2")
    assert "(" not in hardware.describe()


def test_describe_survives_an_empty_dmi_tree(dmi):
    dmi()
    assert hardware.describe()


# ----------------------------------------------------------------- advisory

@pytest.mark.parametrize("vendor,product,expected", [
    ("Alienware", "Alienware m16 R2", True),
    ("Dell Inc.", "Alienware x17 R2", True),
    ("Dell Inc.", "G15 5530", False),
    ("LENOVO", "ThinkPad X1", False),
])
def test_looks_supported_is_advisory_only(dmi, vendor, product, expected):
    """Only a hint for messages. The real test is whether the controllers are
    found by vendor and report shape, which is device.py's job - a Dell G-series
    has AlienFX lighting without saying "Alienware" anywhere in DMI."""
    dmi(sys_vendor=vendor, product_name=product)
    assert hardware.looks_supported() is expected
