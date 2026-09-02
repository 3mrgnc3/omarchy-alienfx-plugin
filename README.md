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
| Power button | AW-ELC `187c:0551`, APIv4 | one colour — **experimental**, see below |

Developed and verified on an **Alienware m16 R2**, BIOS 1.19.0, Omarchy 4.0.2.

## Install

```bash
git clone https://github.com/3mrgnc3/omarchy-alienfx-plugin
cd omarchy-alienfx-plugin
./install.sh
```

The installer is idempotent — re-run it to upgrade in place. It sets up:

- `~/.local/bin/alienfx-ctl` plus its package in `~/.local/share/omarchy-alienfx-plugin/`
- `/etc/udev/rules.d/60-omarchy-alienfx.rules` (the one step needing root)
- the plugin in `~/.config/omarchy/plugins/mrgnc.alienfx/`, enabled on the bar
- a `theme-set` hook drop-in, so theme switches repaint the lights
- systemd `--user` units that restore your lighting at login and after suspend

> **`omarchy plugin add <git-url>` on its own is not enough.** It installs only the QML.
> The udev rule, the CLI and the restore unit all live outside the plugin folder, and
> without them the widget loads but reports `CLI NOT FOUND`. Use `install.sh`.

Uninstall with `./uninstall.sh` (keeps your profiles and keymap; `--purge` removes them).

## The popup

- **KeyMap Wizard** — appears only while no keymap is installed, and self-hides once one
  is. Its first question is whether you already have a keymap file to import; only if you
  decline does it build one by lighting keys one at a time.
- **ThemeSync** *(on by default)* — derives a diagonal gradient from the active theme and
  drives every zone from it. While on, the only other control is brightness, because
  anything else you set would just be overwritten on the next theme switch.
- **ZoneSync** — move all zones together, or turn it off to reveal the zone selector.
- **Zone** — Keyboard / PowerButton / Touchpad / Logo.
- **Colour** — click the swatch for R/G/B sliders. Changes land on the hardware in
  realtime and autosave as you go.
- **Effect** — Gradient (default), Wave, Pulse, Nightrider, Solid.
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

## Known limits

- **The power button is experimental.** Ordinary colour commands are overridden by the
  firmware's own power-state handler. The colour only sticks via a power-state NVRAM
  path, which is written but not yet verified — so treat that zone as best-effort.
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
