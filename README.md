# omarchy-alienfx-plugin

RGB zone control for Alienware laptops, as a native [Omarchy](https://omarchy.org/) 4.0+
Quickshell plugin.

An alien head sits on the bar. Click it and you get a popup that looks like every other
Omarchy widget, because it is built from Omarchy's own components and reads the active
theme at runtime. By default it blends a diagonal gradient from your current theme's
palette across all four lighting zones, and repaints itself whenever you switch themes.

Runs unprivileged. No `sudo`, no root daemon.

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

Only the keyboard is addressable per key, so it carries the real gradient: each key's row
and column are normalised against the keymap extent and averaged for the `tl-br`
diagonal (`t = (row_t + col_t) / 2`), then the two anchor colours are interpolated in
sRGB. Interpolating in sRGB rather than linear space is deliberate — it guarantees the
endpoints are exactly the colours you chose.

The three chassis zones hold one colour each and are sampled at fixed points on that same
axis, which is what makes the whole chassis read as one blend:

| Zone | `t` |
|---|---|
| Lid logo | `0.00` |
| Touchpad | `0.50` |
| Power button | `1.00` |

Anchors come from the theme's `colors.toml`. The near end is the theme's `accent`; the far
end is chosen automatically as its most hue-distant saturated colour, so the blend reads
as a real gradient on any palette. A near-monochrome theme has no distant hue to offer, so
a complement is synthesised instead of collapsing to a flat fill.

Keycaps sit behind a diffuser that visibly desaturates them, so a saturation multiplier
(default `2.4`) is applied before brightness scaling. Tune it with
`alienfx-ctl set --saturation N`.

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

## Device numbering

Node numbers are resolved by USB vendor/product id at runtime, never hard-coded. This is
not hypothetical: on the development laptop `/dev/hidraw0` is a security key, and the
older tooling's hard-coded `hidraw0`/`hidraw1` would have aimed chassis packets at it.
`ALIENFX_ELC_DEV` / `ALIENFX_KBD_DEV` can pin a node by hand; an override still has to
pass the identity check.

## Other machines

Nothing about the zone layout is hard-coded to one model. The machine identifies itself
from DMI (`Alienware m16 R2`, sku, BIOS), and its keymap is stored per model as
`~/.config/omarchy-alienfx-plugin/keymap/alienware-<model>-keymap.json` — so a config
directory can move between machines without them fighting over one file. A plain
`keymap.json` is still honoured for installs that predate this.

A keymap may carry its own `zones` block, e.g.

```json
"zones": { "tpd": [0], "logo": [2], "pbtn": [4] }
```

which is what lets a model with fewer or differently-addressed zones work. **Create New
KeyMap** offers to probe for them: it lights each candidate chassis id in turn and asks
what came on. Where a machine's zone names are unfamiliar the gradient spreads them evenly
across the axis instead of using the reference anchors.

Device nodes are always resolved by USB vendor/product id at runtime, so they follow the
hardware rather than needing configuration.

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
cli/tests/                             135 tests, no hardware required
share/udev/                            the uaccess rule
share/systemd/                         restore + resume units
share/omarchy/hooks/theme-set.d/       the theme-switch hook
share/bin/                             the wizard's terminal launcher
archive/                               previous generations, kept for reference
docs/dead-ends.md                      things that do not work, and why
```

`docs/dead-ends.md` is worth reading before changing the hardware layer. It records
hard-won failures, including two that will brick the keyboard until reboot.

## Development

```bash
cd cli && python3 -m pytest tests -q     # 135 tests, no hardware needed
./cli/bin/alienfx-ctl devices            # run from the checkout, no install
omarchy plugin validate .                # check the manifest
```

The CLI is standard-library only and needs Python 3.11+. Saving a file under
`~/.config/omarchy/plugins/` hot-reloads the plugin; `omarchy restart shell` forces it.

## Licence

MIT — see [LICENSE](LICENSE).
