"""Single-keypress terminal input.

The wizard maps keys by moving a cursor around a drawn keyboard, so it needs
arrow keys as they are pressed rather than lines terminated by Enter. That means
raw mode and decoding escape sequences by hand; there is no smaller way to get
an arrow key out of a POSIX terminal.

Reading is deliberately a plain callable rather than a class, so the wizard can
be driven by a scripted list of keys in tests without a terminal at all.
"""

from __future__ import annotations

import contextlib
import select
import sys

try:
    import termios
    import tty
except ImportError:  # pragma: no cover - Windows has neither
    termios = None
    tty = None

UP, DOWN, LEFT, RIGHT = "up", "down", "left", "right"
PAGE_UP, PAGE_DOWN = "page_up", "page_down"
ENTER, SPACE, BACKSPACE, ESCAPE = "enter", "space", "backspace", "escape"
INTERRUPT = "interrupt"

#: Final byte of a CSI sequence -> the key it means.
_CSI = {"A": UP, "B": DOWN, "C": RIGHT, "D": LEFT}

#: CSI sequences of the form ESC [ <n> ~ . Page keys give a coarse jump, which
#: matters when a guess is far out and stepping by one would take all day.
_CSI_TILDE = {"5": PAGE_UP, "6": PAGE_DOWN}

#: How long to wait for the rest of an escape sequence. A bare Escape and the
#: start of an arrow key are the same first byte, so they are told apart by
#: whether more bytes follow immediately. Generous enough for a slow terminal,
#: short enough that Escape does not feel stuck.
_ESCAPE_TIMEOUT = 0.05


@contextlib.contextmanager
def raw_mode(stream=None):
    """Put the terminal in raw mode for the duration, if it is a terminal.

    Yields whether raw mode was actually entered, so a caller can tell a real
    terminal from a pipe. Always restores the previous settings, including on
    an exception - leaving a terminal raw makes the user's shell unusable.
    """
    stream = stream or sys.stdin
    if tty is None or not _isatty(stream):
        yield False
        return
    fd = stream.fileno()
    saved = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        yield True
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)


def _isatty(stream) -> bool:
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError):
        return False


def read_key(stream=None) -> str:
    """Block for one keypress and return a normalised name.

    Arrows come back as ``up``/``down``/``left``/``right``; Enter, Space,
    Backspace and Escape by name; Ctrl-C as ``interrupt`` so a caller can treat
    it as "give up" rather than having it raise from inside raw mode. Anything
    else is the character itself, lowercased.

    Returns ``""`` at end of input, which is the signal to stop asking.
    """
    stream = stream or sys.stdin
    first = stream.read(1)
    if not first:
        return ""
    if first in ("\r", "\n"):
        return ENTER
    if first == " ":
        return SPACE
    if first in ("\x7f", "\b"):
        return BACKSPACE
    if first == "\x03":
        return INTERRUPT
    if first != "\x1b":
        return first.lower()

    # Escape: either a bare Escape or the lead-in of a CSI sequence.
    if not _waiting(stream):
        return ESCAPE
    second = stream.read(1)
    if second != "[":
        return ESCAPE
    final = stream.read(1)
    if final in _CSI_TILDE:
        stream.read(1)  # consume the trailing '~'
        return _CSI_TILDE[final]
    return _CSI.get(final, ESCAPE)


def _waiting(stream) -> bool:
    """True if more input is already available, without consuming it."""
    try:
        ready, _, _ = select.select([stream], [], [], _ESCAPE_TIMEOUT)
        return bool(ready)
    except (OSError, ValueError):
        # Not selectable - a StringIO in a test, say. Its buffered content is
        # already there, so treat it as available.
        return True


def from_sequence(keys):
    """A ``read_key``-shaped callable that replays a list of keys, then ends.

    The seam the wizard's tests drive it through: no terminal, no raw mode, and
    a flow that terminates instead of blocking for input that never comes.
    """
    pending = list(keys)

    def reader(stream=None):
        return pending.pop(0) if pending else ""

    return reader
