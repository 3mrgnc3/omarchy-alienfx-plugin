# omarchy-alienfx-plugin

RGB zone control for Alienware laptops, as a native [Omarchy](https://omarchy.org/) 4.0+
Quickshell plugin.

An alien head sits on the bar. Click it and you get a popup that looks like every other
Omarchy widget, because it is built from Omarchy's own components and reads the active
theme at runtime. By default it blends a diagonal gradient from your current theme's
palette across all four lighting zones, and repaints itself whenever you switch themes.

Runs unprivileged. No `sudo`, no root daemon.

## Which laptops it works on

**Fully tested on one machine: an Alienware m16 R2.** Everything else is
work in progress, and help is genuinely wanted.

The parts that are the same on every Alienware are already model-independent.
The lighting protocol doesn't vary. The controllers are found by their USB
vendor and the shape of the HID reports they declare, not by a hard-coded
product id, so a different model's controller should still be recognised.
Chassis zones — touchpad ring, lid logo, power button, Tron strips, anything
else — come from a per-model file rather than the code, and laptops that light
the keyboard as four zones instead of per key are supported too.

The one thing that genuinely differs between machines is **which LED belongs to
which key**, and that cannot be worked out remotely. LED numbering follows the
circuit board, not the keyboard: on the tested machine `backspace` is 34 where
the obvious pattern says 33, and the left arrow is 133 where a formula predicts
113. So this plugin ships no LED numbers that anybody guessed — a wrong number
silently lights the wrong key, which is worse than shipping none.

Instead there's a **guided wizard**. It lights one LED at a time and you walk it
onto the right key with the arrow keys; it guesses the next one for you, and
learns from your corrections. Ten minutes, and your machine is mapped.

**If you own a different AlienFX laptop, please send the keymap back.** Adding a
model is a data change with no code behind it: drop the file in and every owner
of that machine gets it automatically. [CONTRIBUTING.md](CONTRIBUTING.md) walks
through generating one and submitting it, and bug reports from unfamiliar
hardware are just as welcome — `alienfx-ctl devices` prints everything needed to
diagnose a machine I can't see.

## What it controls

| Zone | Controller | Addressing |
|---|---|---|
| Keyboard | Darfon `0d62:d2b1`, APIv5 | per key |
| Touchpad halo | AW-ELC `187c:0551`, APIv4 | one colour |
| Lid logo | AW-ELC `187c:0551`, APIv4 | one colour |
| Power button | AW-ELC `187c:0551`, APIv4 | one colour, via NVRAM power states |

Developed and verified on an **Alienware m16 R2**, BIOS 1.19.0, Omarchy 4.0.2.

## Install

Two ways, both supported.

### From GitHub, the Omarchy way

```bash
omarchy plugin add https://github.com/3mrgnc3/omarchy-alienfx-plugin.git --enable
```

That clones the repo into `~/.config/omarchy/plugins/mrgnc.alienfx/` and puts the
alien head on the bar. Click it and the popup will offer **Complete setup**, because
`omarchy plugin add` installs only the QML — the udev rule that makes the hardware
reachable without `sudo`, the CLI that drives it, and the systemd unit that restores
your lighting at login all live outside the plugin folder.

**Complete setup** opens a terminal and runs the installer bundled in the clone. It
checks dependencies first, tells you what is missing, and asks before changing
anything. The udev step needs your password once.

### From a clone

```bash
git clone https://github.com/3mrgnc3/omarchy-alienfx-plugin
cd omarchy-alienfx-plugin
./install.sh
```

`--yes` installs missing dependencies without asking; `--no-deps` reports them and
carries on regardless. Either way the installer is idempotent — re-run it to upgrade
in place.

It sets up:

- `~/.local/bin/alienfx-ctl` plus its package in `~/.local/share/omarchy-alienfx-plugin/`
- `/etc/udev/rules.d/60-omarchy-alienfx.rules` (the one step needing root)
- the plugin in `~/.config/omarchy/plugins/mrgnc.alienfx/`, enabled on the bar
- a `theme-set` hook drop-in, so theme switches repaint the lights
- systemd `--user` units that restore your lighting at login and after suspend

### Dependencies

The installer checks these and separates the ones it cannot work without from the ones
that merely degrade something:

| Needed for | Package |
|---|---|
| **Required** — Omarchy itself, systemd, Python 3.11+ (`tomllib` reads theme palettes), and `sudo` or `pkexec` for the udev step | `python` |
| Recommended — a Nerd Font, or the alien head renders as a blank box | `ttf-jetbrains-mono-nerd` |
| Recommended — a terminal, for the KeyMap Wizard | `alacritty` |
| Optional — `gum`, only to make the installer's prompts nicer | `gum` |

Missing packages are installed with `omarchy pkg add`, falling back to `pacman`.

### Uninstall

```bash
./uninstall.sh            # keeps your profiles and keymap
./uninstall.sh --purge    # removes those too
```

Run this **before** `omarchy plugin remove mrgnc.alienfx`. That command deletes the
plugin folder, which is all Omarchy knows about — the CLI, the udev rule and the
systemd units live outside it and would be left behind. `uninstall.sh` removes the
folder for you via `omarchy plugin remove` anyway, unless it is running from inside
it, in which case it tells you to finish with that command.

Removing the udev rule does not revoke an ACL that is already applied; the device
nodes lose it at the next reboot or replug.

## The popup

- **Complete setup** — appears only when the plugin can see that setup is unfinished:
  either there is no CLI at all, or there is one but the device nodes are not writable
  because the udev rule was never installed. Both leave the lights dead and look
  identical from the outside, so both offer the same fix.
- **KeyMap Wizard** — a small keyboard icon sits top-right, inline with the title, and is
  always available: re-running the wizard is how you repair or replace a keymap. A larger
  labelled button also appears while no keymap is installed for this machine, and self-hides
  once one is. Either opens a terminal with a menu: **1. Load Existing KeyMap File**,
  **2. Create New KeyMap**.
- **ThemeSync** *(on by default)* — derives a diagonal gradient from the active theme and
  drives every zone from it. While on, the only other control is brightness, because
  anything else you set would just be overwritten on the next theme switch.
- **ZoneSync** — move all zones together, or turn it off to reveal the zone selector.
- **Zone** — Keyboard / PowerButton / Touchpad / Logo.
- **Colour range** — two swatches on one row, **FROM** and **TO**, being the two ends of
  the gradient. Click either to point the R/G/B sliders at it; changes land on the hardware
  in realtime and autosave as you go. Until you set the far end it shows greyed-out,
  previewing the complement that would be derived. Choosing **Solid** hides the far end
  entirely, since a flat colour has no second end.
- Picking a colour for a single zone switches the effect to Solid, because Gradient derives
  every zone from its two anchors and would compute a per-zone pick away.
- **Effect** — Gradient (default), Wave, Pulse, Nightrider, Solid. **Wave, Pulse and
  Nightrider animate between both ends of the range** when you have set a far colour, and
  stay single-colour when you have not. The chassis samples the same range so the whole
  machine reads as one blend.
- **Profile** — Load and Save. Saving over the loaded name overwrites it; typing a new
  name creates a new profile. A loaded profile persists across reboots.

## The gradient

Only the keyboard is addressable per key, so it carries the real gradient. Each key's row
and column are normalised against the keymap extent and averaged for the `tl-br` diagonal
(`t = (row_t + col_t) / 2`).

**The blend holds near each anchor and crosses over quickly through the middle.** An
evenly-spaced blend looked wrong, and not because of the colour maths: key density along
the diagonal peaks in the middle — 43 of 85 keys sit between `t=0.4` and `t=0.6`, and three
keys sit at the two extremes — so an even ramp spends most of its *surface* on the
intermediate hues. Measured before easing, 73 of 85 keys were mid-blend and each chosen
colour showed on about six. An S-curve on the blend position takes that to 30 / 35 / 20.

**Interpolation is gamut-mapped OkLCh**, holding chroma and rotating hue the short way
round. A straight line through Oklab's `a`/`b` passes close to neutral between two
well-separated hues, so the keyboard averaged only 47% of the saturation its anchors were
set to and no amount of saturating them could lift it. Holding chroma removes that ceiling
(47% → 94%), at the cost of travelling *through* the intervening hues rather than
desaturating past them: red to teal goes by way of orange and yellow, not by way of grey.
Chroma is fitted to the sRGB gamut per key — clipping channels instead is what made an
earlier attempt come out olive. Endpoints are returned byte-exactly, so the corner keys are
always the colours you chose.

The chassis zones take the **exact colour of the corner key** they sit beside rather than a
fixed point on the axis: the touchpad matches `esc`, the power button matches the
bottom-right key. Sampling `t=1.0` is not good enough — the right arrow sits at `t=0.969`,
because the media keys reach a wider column.

Anchors come from the theme's `colors.toml`. The near end is the theme's `accent`; the far
end is its most hue-distant saturated colour, so the blend reads as a real gradient on any
palette. A near-monochrome theme has no distant hue to offer, so a complement is
synthesised rather than collapsing to a flat fill.

### Intensity

Keycaps sit behind a diffuser that mixes white into everything, so a colour that looks
right on screen reads washed out on the keys. **Intensity** is an absolute saturation
target and the only saturation control — it replaced a multiplier, a floor and a relative
trim that overlapped and fought each other:

| slider | saturation | keyboard delivers |
|---|---|---|
| −10 | 0.40 | 0.41 — muted |
| 0 | 0.85 | 0.86 — the default, calibrated by eye on hardware |
| +10 | 1.00 | 0.94 — as vivid as sRGB allows |

Two straight segments meeting at the centre, so each half is evenly spaced. It applies in
every mode, because it lands in the one shaping funnel. `alienfx-ctl set --intensity N`.

It is applied to the **two anchor colours**, never to each interpolated key. Doing the
latter collapsed the blend into three flat bands, because the trim clamps and clamping
every key erases the saturation ramp a blend is made of.

## CLI

Everything the popup does is available by hand, which makes the whole thing debuggable
without the shell running.

```bash
alienfx-ctl devices                    # what was found, and is it writable?
alienfx-ctl state                      # current settings
alienfx-ctl state --json               # what the plugin reads

alienfx-ctl solid --color ff7800 --brightness 10
alienfx-ctl solid --color '255,120,0' --zones kbd,logo
alienfx-ctl off --zones all
alienfx-ctl effect nightrider --color red --speed slow

alienfx-ctl theme show                 # the anchors derived from the active theme
alienfx-ctl theme apply                # paint the theme gradient now
alienfx-ctl themesync on|off|status
alienfx-ctl zonesync on|off

alienfx-ctl profile list|save NAME|load NAME|rename OLD NEW|delete NAME
alienfx-ctl keymap status|show|import PATH|wizard
alienfx-ctl restore                    # what the systemd unit runs

alienfx-ctl set --color 00ff88 --zones logo --dry-run -v   # show, write nothing
```

Colours accept `RRGGBB`, `#RRGGBB`, `r,g,b`, a name (`orange`, `cyan`, …) or `@themekey`
to pull straight from the active palette. Brightness accepts `0-255` or `10%`.

Add `--persist` to also write chassis colours into NVRAM so they survive a cold boot.

## Why no `sudo`

A udev rule tags both controllers with `uaccess`, which makes systemd put an ACL on the
device nodes for whoever is logged into the local seat.

**The `60-` prefix on that rule is load-bearing.** The ACL is applied by the `uaccess`
builtin invoked from `/usr/lib/udev/rules.d/73-seat-late.rules`, which only sees tags
already set when it runs. A rule numbered above 73 adds the tag too late, the builtin
never fires, and the keyboard node stays root-only — which looks exactly like a
boot-time race and is not one. The previous generation of this tool shipped its rule as
`99-` and worked around the symptom with a retrigger service and a `sleep`.

Verify with `ls -la /dev/hidraw*` — a trailing `+` on the permissions means the ACL is
there.

## Device detection

Controllers are found by **USB vendor id plus HID report shape**, never by product id.
Product ids differ across models — the chassis answers on `0x0550` as well as `0x0551`,
Darfon keyboards on `0xcabc` and `0xdabc` as well as `0xd2b1` — so pinning one made the
tool work on exactly one laptop. Vendor ids do not differ, and the protocol declares its
own generation in its report descriptor:

| controller | vendor | signature | API |
|---|---|---|---|
| chassis | `0x187c` Alienware | output report `0x00`, 34 bytes | v4 |
| keyboard | `0x0d62` Darfon | feature report `0xcc`, 64 bytes | v5 |

Both halves are required. Vendor alone is too loose — Alienware and Dell ship several HID
devices, and this laptop carries two unrelated Dell nodes. Of the ten HID devices present,
only the two real controllers match. `verify_node` re-checks both immediately before the
first write, because `/dev/hidraw0` here is sometimes a security key and the older
tooling's hard-coded `hidraw0`/`hidraw1` would have aimed chassis packets at it.

`alienfx-ctl devices` prints every node with its report shape, which is what explains why
a node was or was not chosen on an unfamiliar machine. `ALIENFX_ELC_DEV` /
`ALIENFX_KBD_DEV` pin a node by hand; an override still has to pass the same check.

## Other machines, in detail

See [Which laptops it works on](#which-laptops-it-works-on) for the short
version and [CONTRIBUTING.md](CONTRIBUTING.md) for how to add yours. This
section is the mechanism.

Nothing is hard-coded to one model. The machine identifies itself from DMI
(`Alienware m16 R2`, sku, BIOS) and its keymap is stored per model as
`~/.config/omarchy-alienfx-plugin/keymap/alienware-<model>-keymap.json`, so a config
directory can move between machines without them fighting over one file. A plain
`keymap.json` is still honoured for installs that predate this.

A keymap contributed for your exact model, bundled in `cli/src/alienfx_ctl/data/`
under the same name, is used ahead of the reference map — so adding support for a
machine is a data change with no code behind it. Your own probed keymap always
wins over a bundled one.

**Zones come from the keymap**, not from a constant:

```json
"zones": { "tpd": [0], "logo": [2], "pbtn": [4] }
```

Any names work. A model with Tron strips or a second lid light is driven by shipping a
keymap that lists them — no code change — and where the names are unfamiliar the gradient
spreads them evenly along the blend axis rather than using the reference anchors. The order
they are listed in is the order the gradient travels through them.

**Keyboards lit as four zones** — most older Alienware laptops, which have no per-key
controller at all — are not a special case. Such a machine is simply one with more chassis
zones and no keyboard:

```json
"keyboard": "zones",
"zones": { "kb1": [2], "kb2": [3], "kb3": [4], "kb4": [5], "logo": [1], "pbtn": [0] }
```

It gets a theme gradient across the four keyboard zones from that data alone. An absent
`keyboard` field means per-key.

**Create New KeyMap** offers to probe the chassis zones, lighting each candidate id in turn
and asking what came on, and it will save a zone-only keymap on a machine with no per-key
controller.

### Bundled keyboard shapes

The wizard needs the shape of your keyboard before it can ask which LED belongs to which
key, so three are bundled:

| id | keys | tested |
|---|---|---|
| `m16-r2` | 85 | yes, probed on real hardware |
| `compact` | 81 | no — `m16-r2` without the media column (14/15 inch) |
| `numpad` | 102 | no — `m16-r2` plus a standard keypad block (16/17 inch) |

These carry **no LED indices**, and that is deliberate rather than laziness. Indices are
irregular on real hardware: on the reference machine row bases are near-multiples of 20
(0, 20, 40, 61, 81, 100) but within rows they skip — `backspace` is 34 where the pattern
says 33, the left arrow is 133 where a formula predicts 113, and the media keys sit alone
at 156–159. That is PCB routing, not logic, and a formula fitting 34 of 85 keys is not a
rule. Every tool in this space probes for them instead, and so does this one. A wrong shape
is visible and skippable; a wrong index silently lights the wrong key.

`alienfx-ctl keymap gaps` reports LED indices the keymap does not name and flags the ones
adjacent to a named key — reach for it first if a single key ever behaves oddly.

## Known limits

- **The power button takes the slow path.** Ordinary colour commands are overridden by
  the firmware's own power-state handler, so a colour only sticks by programming six
  NVRAM state blocks. That is done, and the zone shows your colour on AC and on
  battery, breathes it while charging, and fades it out going to sleep — while still
  warning in red when the battery is critical, which is the one bit of firmware
  behaviour worth keeping. It costs ~2.3s, so it is skipped during a live colour drag
  and whenever the colour already in NVRAM matches.
- **Chassis zones cannot animate.** They are single-LED zones; Wave, Pulse and
  Nightrider are keyboard effects, and the chassis holds the base colour.
- **Brightness is colour.** Neither controller exposes a brightness field on the paths
  used here, so brightness scales the RGB channels. At 10% a saturated colour is a dim
  ember, which is the intent.
- Effects that do **not** render usefully on the reference BIOS — hardware pulse, dual
  wave, laser — are deliberately not offered rather than exposed and broken.

## Layout

```
manifest.json  Panel.qml  Model.js     the Quickshell plugin
cli/src/alienfx_ctl/                   the CLI: device, apiv4, apiv5, gradient, palette, …
cli/src/alienfx_ctl/data/              the reference keymap and the bundled shapes
cli/tests/                             505 tests, no hardware required
share/udev/                            the uaccess rule
share/systemd/                         restore + resume units
share/omarchy/hooks/theme-set.d/       the theme-switch hook
share/bin/                             the wizard's terminal launcher
docs/dead-ends.md                      things that do not work, and why
```

`docs/dead-ends.md` is worth reading before changing the hardware layer. It records
hard-won failures, including two that will brick the keyboard until reboot.

Earlier generations of this tool are kept on the `archive-reference` branch rather than on
`main`: they were 15M of the repo, every user installing the plugin would have cloned
them, and they contain runnable scripts that hit the hardware directly — including the two
failure modes above. Fetch them with
`git checkout archive-reference -- archive/` when you need to look something up.

## Development

Checks run **locally only** — there is no CI, no GitHub Actions, and no remote
runner. Everything is one command:

```bash
cd cli && python3 -m pytest tests -q
```

That covers the protocol buffers, the gradient maths, the wizard, and the
packaging checks that used to be a workflow: the manifest against what Omarchy's
plugin registry actually enforces, the declared settings against the ones the
QML reads, the bundled layouts, and the shell scripts parsing. No hardware
needed for any of it.

```bash
cd cli && python3 -m pytest tests -q     # 505 tests, no hardware needed
./cli/bin/alienfx-ctl devices            # run from the checkout, no install
omarchy plugin validate .                # check the manifest
```

The CLI is standard-library only and needs Python 3.11+. Saving a file under
`~/.config/omarchy/plugins/` hot-reloads the plugin; `omarchy restart shell` forces it.

## Licence

MIT — see [LICENSE](LICENSE).
