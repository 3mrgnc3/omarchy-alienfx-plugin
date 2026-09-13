"""The physical keyboard as a grid of keys, and how to draw it.

The wizard needs to show the user a picture of their keyboard and move a cursor
around it, because "which key just lit up?" is much easier to answer by pointing
than by typing a name. This is that picture.

A layout carries the *physical* arrangement - which row a key is on and which
column it occupies - which is exactly what the gradient needs and what differs
between models and regions. Column numbers are real positions, not sequence
numbers: on the reference machine the media keys reach column 16 while the
bottom row's arrow stops at 13, and the diagonal blend depends on that.

The starting template comes from an existing keymap via ``from_keymap`` rather
than a table written out here, so there is one description of the reference
layout in the project and not two that can drift apart.
"""

from __future__ import annotations

import json
import os

from . import gradient

#: Optional blocks of keys that some keyboards have and the reference machine
#: does not.
#:
#: The base shape is deliberately *not* stored as data. It is read from the
#: shipped keymap at runtime, because that file already describes this keyboard
#: completely - storing it a second time meant two copies to keep in step, and a
#: test whose only job was to police the fact that they matched.
#:
#: An earlier version bundled three whole layouts. Measured, they carried 17
#: keys of information between them: one was byte-identical to the shipped
#: keymap's grid, and another was that minus four media keys, which a user
#: without them skips in four keypresses. Only the keypad was ever real, because
#: a key the template lacks cannot be mapped at all - you can skip a key you do
#: not have, but you cannot conjure one.
#:
#: Nothing here carries an LED index. Those are irregular on real hardware and
#: cannot be derived, so they are probed; see docs/dead-ends.md.
LAYOUT_EXTENSIONS = os.path.join(os.path.dirname(__file__), "data", "layout-extensions.json")


class LayoutError(ValueError):
    """Raised for a grid that cannot be built or navigated."""


#: How wide each drawn cell is, including its separator. Four characters of
#: name is enough to tell keys apart at a glance ("back", "spac", "lshi").
_CELL = 6
_NAME = 4


class Layout:
    """Rows of ``(name, column)`` pairs, ordered left to right within a row."""

    def __init__(self, rows, hints=None):
        self.rows = [list(row) for row in rows if row]
        if not self.rows:
            raise LayoutError("layout has no rows")
        #: ``{key: fn-legend}``, e.g. ``f7 -> kbd_backlight``. Shown while
        #: mapping, because "F7" alone is not how anyone identifies the key
        #: they are looking at. Carried through so a re-run does not lose it.
        self.hints = dict(hints or {})

    # ----------------------------------------------------------- building

    @classmethod
    def from_keymap(cls, data):
        """Build a layout from a keymap's ``grid_positions``.

        Used both for the "start from the built-in template" path and for
        re-running the wizard against a keymap that already exists.
        """
        positions = (data or {}).get("grid_positions") or {}
        if not positions:
            raise LayoutError("keymap has no grid_positions to build a layout from")
        by_row: dict = {}
        for name, position in positions.items():
            row, col = gradient._row_col(position)
            by_row.setdefault(row, []).append((name, col))
        hints = {key: str(value)
                 for key, value in ((data or {}).get("secondary_functions") or {}).items()
                 if value and str(value).strip() not in ("", ".")}
        return cls([sorted(by_row[row], key=lambda pair: pair[1])
                    for row in sorted(by_row)], hints)

    @classmethod
    def from_rows(cls, rows):
        """Build from rows of bare names, assigning columns left to right.

        For a machine with no template to start from. Columns come out as
        sequence numbers, which is the best that can be inferred from names
        alone; a key that spans or a gap in the row is not represented.
        """
        return cls([[(str(name).strip().lower(), col)
                     for col, name in enumerate(row) if str(name).strip()]
                    for row in rows])

    # ------------------------------------------------------------ reading

    def keys(self) -> list:
        """Every key name, in reading order."""
        return [name for row in self.rows for name, _ in row]

    def grid_positions(self) -> dict:
        """``{name: {"row": r, "col": c}}``, ready for a keymap file."""
        return {name: {"row": row_index, "col": col}
                for row_index, row in enumerate(self.rows)
                for name, col in row}

    # --------------------------------------------------------- navigating

    def clamp(self, cursor) -> tuple:
        """Bring a cursor inside the grid."""
        row = max(0, min(len(self.rows) - 1, cursor[0]))
        return row, max(0, min(len(self.rows[row]) - 1, cursor[1]))

    def move(self, cursor, direction) -> tuple:
        """The cursor after an arrow press.

        Vertical movement keeps the cursor under the same *column* rather than
        the same list position, so it tracks the key physically above or below
        even where rows have different numbers of keys. Anything else feels
        broken on a keyboard, where rows are famously ragged.
        """
        row, index = self.clamp(cursor)
        if direction == "left":
            return row, max(0, index - 1)
        if direction == "right":
            return row, min(len(self.rows[row]) - 1, index + 1)
        if direction in ("up", "down"):
            step = -1 if direction == "up" else 1
            target_row = row + step
            if not 0 <= target_row < len(self.rows):
                return row, index
            column = self.rows[row][index][1]
            nearest = min(range(len(self.rows[target_row])),
                          key=lambda i: abs(self.rows[target_row][i][1] - column))
            return target_row, nearest
        return row, index

    def name_at(self, cursor) -> str:
        row, index = self.clamp(cursor)
        return self.rows[row][index][0]

    # ------------------------------------------------------------ drawing

    def render(self, cursor=None, assigned=None) -> str:
        """The keyboard as text.

        The cursor is bracketed, an already-assigned key is dimmed to dots
        under its name, so the user can see at a glance what is left to do.
        """
        cursor = self.clamp(cursor) if cursor else None
        assigned = assigned or {}
        lines = []
        for row_index, row in enumerate(self.rows):
            cells = []
            for index, (name, col) in enumerate(row):
                label = name[:_NAME]
                if cursor and (row_index, index) == cursor:
                    cell = f"[{label:^{_NAME}}]"
                elif name in assigned:
                    cell = f" {label:^{_NAME}}."
                else:
                    cell = f" {label:^{_NAME}} "
                # Indent by real column so the drawn shape matches the machine.
                cells.append((col, cell))
            line = ""
            for col, cell in cells:
                line = line.ljust(col * _CELL) + cell
            lines.append(line)
        return "\n".join(lines)


#: Names that do not read well shouted back at the user. Everything not listed
#: is uppercased, which covers letters, digits and the f-row. Kept here beside
#: the grid rather than in the wizard, so there is one place that knows how a
#: key is spelled and how it is shown.
LABELS = {
    "grave": "`  ~", "minus": "-  _", "equal": "=  +",
    "lbracket": "[  {", "rbracket": "]  }", "backslash": "\\  |",
    "semicolon": ";  :", "apostrophe": "'  \"",
    "comma": ",  <", "dot": ".  >", "slash": "/  ?",
    "space": "SPACE BAR", "enter": "ENTER", "backspace": "BACKSPACE",
    "capslock": "CAPS LOCK", "tab": "TAB", "esc": "ESC", "delete": "DELETE",
    "lshift": "LEFT SHIFT", "rshift": "RIGHT SHIFT",
    "lctrl": "LEFT CTRL", "rctrl": "RIGHT CTRL",
    "lalt": "LEFT ALT", "ralt": "RIGHT ALT",
    "lmeta": "LEFT SUPER / WINDOWS", "winlock": "WINDOWS LOCK",
    "fn": "FN", "up": "UP ARROW", "down": "DOWN ARROW",
    "left": "LEFT ARROW", "right": "RIGHT ARROW",
    "micmute": "MIC MUTE  (right edge)", "mute": "SPEAKER MUTE  (right edge)",
    "volumeup": "VOLUME UP  (right edge)", "volumedown": "VOLUME DOWN  (right edge)",
    "home": "HOME", "end": "END",
    "numlock": "NUM LOCK", "kpslash": "KEYPAD  /", "kpasterisk": "KEYPAD  *",
    "kpminus": "KEYPAD  -", "kpplus": "KEYPAD  +", "kpenter": "KEYPAD ENTER",
    "kpdot": "KEYPAD  .",
    **{f"kp{digit}": f"KEYPAD  {digit}" for digit in range(10)},
}


def label_for(name: str) -> str:
    """How a key should be described to someone looking at their keyboard."""
    return LABELS.get(name, name.upper())


def reference() -> "Layout":
    """The keyboard this project was probed on, from the shipped keymap.

    The starting point the wizard offers. Built from the keymap rather than
    duplicated as data, so the two cannot drift apart.
    """
    from . import keymap
    return Layout.from_keymap(keymap.load_file(keymap.SHIPPED_KEYMAP))


def extensions() -> dict:
    """``{id: spec}`` for every optional block of extra keys."""
    with open(LAYOUT_EXTENSIONS, encoding="utf-8") as handle:
        return json.load(handle).get("extensions") or {}


def extend(grid: "Layout", name: str) -> "Layout":
    """A copy of ``grid`` with a named block of keys added.

    Rows are matched by index and the new keys sorted into place by column, so
    a keypad lands to the right of the keys already there.
    """
    spec = extensions().get(name)
    if spec is None:
        raise LayoutError(f"no layout extension named {name!r}")

    rows = [list(row) for row in grid.rows]
    for key, row_index, col in spec["keys"]:
        while len(rows) <= int(row_index):
            rows.append([])
        rows[int(row_index)].append((str(key), int(col)))
    return Layout([sorted(row, key=lambda pair: pair[1]) for row in rows],
                  grid.hints)
