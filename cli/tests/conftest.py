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
