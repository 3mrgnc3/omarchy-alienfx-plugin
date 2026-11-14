import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


@pytest.fixture()
def config_root(tmp_path, monkeypatch):
    """Point the whole config tree at a temp dir so tests never touch real state."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from alienfx_ctl import state
    state.ensure_dirs()
    return state.config_root()


@pytest.fixture()
def shipped_keymap():
    """The real m16 R2 keymap, so coverage tests run against actual data."""
    import json
    from alienfx_ctl import keymap
    with open(keymap.SHIPPED_KEYMAP, encoding="utf-8") as handle:
        return json.load(handle)


#: The anchor pair every gradient measurement in this suite was taken against -
#: the `aetheria` theme's accent and the secondary the palette picks for it.
#: The numbers quoted in the gradient tests' docstrings mean nothing without
#: knowing which two colours produced them, so they are named here.
MEASURED_ANCHORS = ((190, 63, 80), (63, 190, 173))


@pytest.fixture(autouse=True)
def stable_theme_anchors(monkeypatch):
    """Pin the ThemeSync anchors for every test.

    `state.DEFAULT_STATE` has `themesync` on, so any test that builds a state
    from it and calls `engine.plan` resolves its anchors through
    `palette.anchors()` - which reads the *host machine's* live Omarchy theme.
    That made most of the gradient suite quietly dependent on which theme the
    developer happened to have loaded, and it failed outright on CI, where there
    is no theme at all.

    Patching here rather than in each test keeps the ThemeSync code path under
    test while making its input deterministic. Tests that want the manual path
    set `themesync=False` and supply their own colours, and the palette tests
    exercise the selection logic directly, so neither is affected by this.
    """
    from alienfx_ctl import palette
    monkeypatch.setattr(palette, "anchors", lambda: MEASURED_ANCHORS)


#: The keymap suite exercises `load()`'s precedence rules itself, so it has to see
#: the real thing; the same for the hardware tests that save a map.
_REAL_KEYMAP_MODULES = {"test_keymap.py", "test_hardware.py"}


@pytest.fixture(autouse=True)
def reference_keymap(monkeypatch, request):
    """Pin what `keymap.load()` returns for everything outside the keymap suite.

    `load()` prefers the user's own map, then one contributed for this exact
    model, then the reference. The first two depend on the machine the suite is
    running on, so the moment a keymap is contributed for model X, the zone-name
    and index assumptions in the apply and gradient suites fail on model X
    machines while still passing on the machine the suite was written on - and a
    contributor only finds out after their map is already in the tree.

    Pinning here leaves the load path itself under test in `test_keymap.py`
    while making what the planner sees deterministic for everyone else.
    """
    if os.path.basename(str(request.node.fspath)) in _REAL_KEYMAP_MODULES:
        return
    from alienfx_ctl import keymap
    reference = keymap.load_file(keymap.SHIPPED_KEYMAP)
    monkeypatch.setattr(keymap, "load", lambda: reference)

