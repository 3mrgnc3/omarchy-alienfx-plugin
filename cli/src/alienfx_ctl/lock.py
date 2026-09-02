"""Serialising hardware access between concurrent invocations.

Every change spawns a short-lived process, and a live colour drag fires them
back to back. A chassis write takes roughly 190ms (the ioctl alone blocks
~62ms per packet), so two invocations can easily overlap - and two processes
interleaving SET_REPORTs on the same controller is how you get half-applied
colour, or a keyboard stuck mid-sequence.

An flock makes that impossible. The policy differs by caller:

* an intermediate frame of a drag (``fast``) should *drop* if the hardware is
  busy - the next frame supersedes it, so waiting only adds latency;
* anything else waits briefly, because it is a discrete action the user
  expects to land.
"""

from __future__ import annotations

import contextlib
import errno
import fcntl
import os
import time

from . import state

LOCK_NAME = "apply.lock"


def lock_path() -> str:
    return os.path.join(state.config_root(), LOCK_NAME)


class Busy(RuntimeError):
    """Raised when the hardware is in use and the caller chose not to wait."""


@contextlib.contextmanager
def hardware_lock(wait: float = 2.0, drop_if_busy: bool = False):
    """Hold the hardware lock for the duration of the block.

    ``drop_if_busy`` raises :class:`Busy` immediately rather than waiting.
    """
    state.ensure_dirs()
    handle = open(lock_path(), "w", encoding="utf-8")
    deadline = time.monotonic() + max(0.0, wait)
    try:
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN):
                    raise
                if drop_if_busy:
                    raise Busy("hardware busy") from None
                if time.monotonic() >= deadline:
                    raise Busy("hardware busy: timed out waiting for the lock") from None
                time.sleep(0.02)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()
