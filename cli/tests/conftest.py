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
