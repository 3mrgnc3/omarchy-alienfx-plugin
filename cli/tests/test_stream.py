"""Stream mode: many applies through one process.

Spawning an interpreter and reopening the chassis node per change costs about
100ms before any work happens, which is most of a colour-drag frame. Stream
mode removes both. These tests drive the real process over a pipe, because the
behaviour that matters is how it survives a whole session of input.
"""

import os
import subprocess
import sys

import pytest

SRC = os.path.join(os.path.dirname(__file__), "..", "src")


@pytest.fixture()
def stream(tmp_path):
    env = dict(os.environ)
    env["XDG_CONFIG_HOME"] = str(tmp_path)
    env["PYTHONPATH"] = os.path.abspath(SRC)
    proc = subprocess.Popen(
        [sys.executable, "-c",
         "from alienfx_ctl.cli import main; import sys; sys.exit(main())", "stream"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1, env=env,
    )
    yield proc
    if proc.poll() is None:
        try:
            proc.stdin.close()
        except Exception:
            pass
        proc.wait(timeout=5)


def send(proc, line):
    """Send one command and return its status line.

    A command's own output goes to stdout ahead of its status, so the reader
    consumes lines until the terminator - `ok` or `err ...` - rather than
    assuming one line per command.
    """
    proc.stdin.write(line + "\n")
    proc.stdin.flush()
    while True:
        reply = proc.stdout.readline()
        if reply == "":
            raise AssertionError("stream closed without a status line")
        reply = reply.strip()
        if reply == "ok" or reply.startswith("err"):
            return reply


def test_a_command_that_needs_no_hardware_succeeds(stream):
    assert send(stream, "keymap show").startswith("ok")


def test_an_unknown_command_does_not_kill_the_stream(stream):
    """argparse exits the process on a bad command. In a stream that would drop
    the session, so it has to be caught."""
    assert send(stream, "nonsense --wat").startswith("err")
    # still alive and serving
    assert send(stream, "keymap show").startswith("ok")


def test_a_bad_argument_value_is_reported_not_fatal(stream):
    assert send(stream, "set --color notacolour").startswith("err")
    assert send(stream, "keymap show").startswith("ok")


def test_unbalanced_quoting_is_reported(stream):
    assert "quoting" in send(stream, 'set --color "ff0000')
    assert send(stream, "keymap show").startswith("ok")


def test_stream_cannot_nest(stream):
    """A nested stream would consume the same stdin and deadlock the caller."""
    assert send(stream, "stream").startswith("err")
    assert send(stream, "keymap show").startswith("ok")


def test_blank_lines_and_comments_are_ignored(stream):
    stream.stdin.write("\n#  a comment\n")
    stream.stdin.flush()
    # Neither produced a reply, so this reply belongs to the real command.
    assert send(stream, "keymap show").startswith("ok")


def test_quit_exits_cleanly(stream):
    stream.stdin.write("quit\n")
    stream.stdin.flush()
    assert stream.wait(timeout=5) == 0


def test_closing_stdin_exits_cleanly(stream):
    """This is the normal path: the popup closes and the pipe goes away."""
    stream.stdin.close()
    assert stream.wait(timeout=5) == 0
