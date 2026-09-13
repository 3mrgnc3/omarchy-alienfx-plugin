# Contributing

The most useful thing anyone can contribute is **a keymap for a laptop I don't
own**.

Everything else about this plugin is model-independent. The lighting protocol is
the same across Alienware machines, the controllers are found by their USB
vendor and the shape of the HID reports they declare, and the colour work has no
idea what model it is running on. The one thing that genuinely differs from
machine to machine is which LED belongs to which key — and that is the one thing
that cannot be worked out from a distance.

## Why the keymap can't just be guessed

It would be nice if LED numbers followed the keyboard. They don't. On the one
machine this was developed on, the rows start at 0, 20, 40, 61, 81 and 100 —
nearly regular — but inside each row the numbers skip. `backspace` is 34 where
the pattern says 33, every key on the third row is one higher than expected, the
left arrow is 133 where a formula predicts 113, and the four media keys sit off
on their own at 156–159.

That is how the circuit board is wired, not a rule, and a formula that fits 34
of 85 keys is no use at all. Every serious tool in this space asks the user to
probe for these numbers. So does this one.

The consequence matters: **a guessed keymap is worse than none.** A wrong shape
is obvious and you skip past it. A wrong LED number silently lights the wrong
key, and you have no way to tell bad bundled data from broken hardware. That is
why nothing here ships indices anybody guessed.

## Generating a keymap for your machine

You need the laptop in front of you. It takes about ten minutes.

### 1. Install and check the hardware is found

```bash
omarchy plugin add https://github.com/3mrgnc3/omarchy-alienfx-plugin.git --enable
alienfx-ctl devices
```

You should see both controllers resolved and writable:

```
elc     : /dev/hidraw0 (187c:0551) writable
kbd     : /dev/hidraw2 (0d62:d2b1) writable
```

If either says `NOT FOUND`, that is itself worth reporting — send the full
output of `alienfx-ctl devices`, which lists every HID device with its report
shape. That is exactly what is needed to work out whether your machine speaks a
variant not yet recognised.

If one says `NOT writable`, the udev rule didn't install. Re-run `install.sh`,
then unplug nothing and reboot.

### 2. Run the wizard

Click the keyboard icon at the top of the plugin popup, or:

```bash
alienfx-ctl keymap wizard
```

Choose **Create New KeyMap**.

### 3. Pick the closest keyboard shape

The wizard offers bundled shapes — with or without a numeric keypad, with or
without the media column. Pick whichever looks most like your keyboard. It does
not have to be exact: keys you don't have are skipped in a moment, and the shape
only decides which keys you get asked about.

If none of them fit, say so in an issue and include a photo of the keyboard. A
new shape is a small addition.

### 4. Probe the chassis zones

Say yes. The wizard lights one chassis light at a time and asks what came on —
the touchpad ring, the lid logo, the power button, or whatever your model has.
Type a name of your own for anything unfamiliar; Tron strips and second lid
lights are supported, and the order you name them in is the order the gradient
travels through them.

Ten questions, about thirty seconds.

### 5. Map the keys

Every light goes out. The terminal names one key at a time and lights its best
guess in **red**. Move the light onto the right key, press Enter, and it turns
**green** and is saved.

| key | does |
|---|---|
| ← → | move the light one LED |
| PgUp / PgDn | move it ten |
| ↑ ↓ | choose a different key to map |
| Enter | that's the one — save it |
| `s` | skip: this machine has no such key |
| `u` | unassign the key shown |
| `1`–`9` | jump to the start of a row |
| `r` | show what's mapped so far |
| `q` | finish and save |

The guess is the last confirmed number plus one, which is right most of the
time, so this is usually just pressing Enter. Better still, it self-corrects: if
your keyboard's numbering starts at 3, fix it once with the arrows and every
later guess inherits the correction.

Progress is saved after every key, so closing the window or a crash costs you
one key, not the session. Re-running picks up where you left off.

### 6. Check it

```bash
alienfx-ctl keymap show          # how many keys and zones it found
alienfx-ctl keymap gaps          # LED numbers no key claims
alienfx-ctl theme apply --force  # look at the keyboard
```

A good result is a smooth diagonal blend with no dead keys and no key stuck on
a colour of its own. `keymap gaps` flags unnamed LEDs that sit next to named
ones — those are usually a wide key covering two LEDs, where naming the second
one fixes a key that looked half-lit.

Your keymap is now at:

```
~/.config/omarchy-alienfx-plugin/keymap/alienware-<model>-keymap.json
```

### Laptops with a four-zone keyboard

Many older Alienware models light the keyboard as four zones rather than per
key, and have no per-key controller at all. The wizard notices, skips the key
mapping, and offers to save a zone-only keymap after the chassis probe. Those
machines get a theme gradient across their four keyboard zones — there is
nothing else to do, and no keys to map.

## Submitting your keymap

**Send it as a pull request containing one new file.** Nothing else needs to
change — no code, no registration, no edit to any list.

The wizard already wrote the file under exactly the right name, so copy it
across without renaming it:

```bash
cp ~/.config/omarchy-alienfx-plugin/keymap/alienware-*-keymap.json \
   cli/src/alienfx_ctl/data/
```

That gives you a single added file:

```
cli/src/alienfx_ctl/data/alienware-<model>-keymap.json
```

for example `alienware-m15-r3-keymap.json` or `alienware-x17-r2-keymap.json`.
The name is how the plugin finds it: an owner of that model gets your keymap
automatically, ahead of the reference map, while anyone who has probed their own
machine keeps theirs. **Keep the filename the wizard produced** — it is derived
from what the firmware reports about the machine, so a hand-edited name will
simply never match.

### The pull request

One file, and in the description:

1. **Your exact model** — `cat /sys/class/dmi/id/product_name`.
2. **The output of `alienfx-ctl devices`.**
3. **What actually works.** Does the gradient run corner to corner? Are all the
   keys lit? Do the chassis zones light the things they claim to? Say what is
   wrong as well as what is right — a keymap covering 80 of 90 keys is still
   worth having, and being straight about the gaps saves the next owner of that
   machine repeating your work.

A photo of the lit keyboard is the most convincing evidence there is, if you
don't mind taking one.

Please don't include your `current.json`, profiles, or anything else from your
config directory. One keymap file per pull request, so each model can be
accepted or discussed on its own.

### What is checked

The local test suite validates every bundled keymap, so a malformed file cannot
reach a release:

```bash
cd cli && python3 -m pytest tests -q
```

Run it before opening the pull request. It needs no hardware and no network.

Beyond that, review is mostly taking your word for it — nobody here has your
laptop. That is exactly why the honest account of what works matters more than a
tidy diff.

## Other contributions

**Bug reports** are most useful with `alienfx-ctl devices`, `alienfx-ctl state`
and `alienfx-ctl keymap show` attached, plus which theme you were on if it looks
like a colour problem.

**Keyboard shapes** for layouts not covered — see step 3.

**Code**: run the tests before and after.

```bash
cd cli && python3 -m pytest tests -q
```

They are local only. There is no CI, no GitHub Actions, and no remote runner,
and none is wanted. Everything runs offline and nothing needs hardware — the
protocol tests capture the bytes that would have reached the device and assert
on them.

**Please read `docs/dead-ends.md` before changing anything in the hardware
layer.** It records things that were tried and did not work, including two that
will brick the keyboard until you reboot, and several that looked like bugs but
were not. It exists so the same afternoon isn't lost twice.

Two rules worth stating plainly:

- **Never ship an LED index nobody has verified on the hardware it describes.**
  See the top of this file.
- **Never take light numbers from other tooling.** The number stored here is not
  the number that goes on the wire — this code sends `index + 1` — and half the
  fields in a keymap here exist to serve this renderer and have no equivalent
  anywhere else. The two cannot be lined up without the hardware in front of
  you, and at that point the wizard has already answered the question properly.
