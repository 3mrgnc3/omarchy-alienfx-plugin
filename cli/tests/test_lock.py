"""Serialising hardware access.

A live colour drag fires one short-lived process after another, and a chassis
write takes ~190ms, so overlap is the normal case rather than the exception.
Two processes interleaving SET_REPORTs is how you get half-applied colour.
"""

import multiprocessing
import time

import pytest

from alienfx_ctl import lock


def test_lock_is_exclusive_within_a_process(config_root):
    with lock.hardware_lock():
        with pytest.raises(lock.Busy):
            with lock.hardware_lock(wait=0.0, drop_if_busy=True):
                pass


def test_a_drag_frame_drops_immediately_rather_than_queueing(config_root):
    with lock.hardware_lock():
        started = time.monotonic()
        with pytest.raises(lock.Busy):
            with lock.hardware_lock(wait=0.0, drop_if_busy=True):
                pass
        # Must not sit and wait: the next frame supersedes this one anyway.
        assert time.monotonic() - started < 0.1


def test_a_deliberate_action_gives_up_after_its_timeout(config_root):
    with lock.hardware_lock():
        started = time.monotonic()
        with pytest.raises(lock.Busy):
            with lock.hardware_lock(wait=0.15):
                pass
        assert time.monotonic() - started >= 0.15


def test_lock_is_released_even_when_the_body_raises(config_root):
    with pytest.raises(ValueError):
        with lock.hardware_lock():
            raise ValueError("boom")
    # Must be acquirable again straight away.
    with lock.hardware_lock(wait=0.0, drop_if_busy=True):
        pass


def _hold(path_env, seconds, ready, done):
    import os
    os.environ["XDG_CONFIG_HOME"] = path_env
    from alienfx_ctl import lock as child_lock
    with child_lock.hardware_lock():
        ready.set()
        time.sleep(seconds)
    done.set()


def test_lock_excludes_a_separate_process(tmp_path, monkeypatch):
    """The real case: two `alienfx-ctl` invocations, not two threads."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from alienfx_ctl import state
    state.ensure_dirs()

    ctx = multiprocessing.get_context("fork")
    ready, done = ctx.Event(), ctx.Event()
    child = ctx.Process(target=_hold, args=(str(tmp_path), 0.4, ready, done))
    child.start()
    try:
        assert ready.wait(2.0), "child never took the lock"
        with pytest.raises(lock.Busy):
            with lock.hardware_lock(wait=0.0, drop_if_busy=True):
                pass
    finally:
        child.join(3.0)
        if child.is_alive():
            child.terminate()
    # Once the child is gone the lock is free again.
    with lock.hardware_lock(wait=1.0):
        pass
