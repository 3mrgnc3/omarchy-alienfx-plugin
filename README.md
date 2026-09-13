# omarchy-alienfx-plugin

RGB lighting control for Alienware laptops, as a native [Omarchy](https://omarchy.org/)
4.0+ Quickshell plugin.

An alien head sits on the bar. Click it for a popup built from Omarchy's own components,
so it follows your theme. By default it blends a diagonal gradient from the active theme's
palette across the keyboard and the chassis zones, and repaints when you switch themes.

Runs unprivileged. No `sudo`, no root daemon.

## Which laptops

Tested on one machine, an Alienware m16 R2. Everything else is work in progress and help
is welcome.

Most of the plugin is already model independent. The lighting protocol is the same across
Alienware machines, controllers are found by USB vendor and HID report shape rather than a
hard-coded product id, chassis zones come from a per-model file, and laptops that light the
keyboard as four zones instead of per key are supported.

What differs between machines is which LED belongs to which key, and that can't be worked
out remotely. LED numbering follows the circuit board, not the keyboard: on the tested
machine `backspace` is 34 where the obvious pattern says 33, and the left arrow is 133
where a formula predicts 113. So no LED numbers here are guessed. A wrong one silently
lights the wrong key, which is worse than shipping none.

Instead there's a wizard. It lights one LED at a time and you walk it onto the right key
with the arrow keys, guessing the next one and learning from your corrections. Ten minutes
and your machine is mapped.

If you own a different AlienFX laptop, please send the keymap back. Adding a model is a
data change with no code behind it. See [CONTRIBUTING.md](CONTRIBUTING.md).

## What it controls

| Zone | Hardware |
|---|---|
| Keyboard | per-key RGB, 85 keys on the reference machine |
| Touchpad | the halo around it |
| Lid logo | the alien head |
| Power button | the alien head on the button |

## Install

### From GitHub

```bash
omarchy plugin add https://github.com/3mrgnc3/omarchy-alienfx-plugin.git --enable
```

This installs only the QML, so the popup will offer **Complete setup** on first click. The
udev rule, the CLI and the systemd units live outside the plugin folder. Complete setup
opens a terminal, checks dependencies, tells you what's missing and asks before changing
anything. The udev step needs your password once.

### From a clone

```bash
git clone https://github.com/3mrgnc3/omarchy-alienfx-plugin
cd omarchy-alienfx-plugin
./install.sh
```

`--yes` installs missing dependencies without asking, `--no-deps` reports them and carries
on. The installer is idempotent, so re-run it to upgrade in place.

It sets up:

- `~/.local/bin/alienfx-ctl` and its package in `~/.local/share/omarchy-alienfx-plugin/`
- `/etc/udev/rules.d/60-omarchy-alienfx.rules`, the one step needing root
- the plugin in `~/.config/omarchy/plugins/3mrgnc3.alienfx/`, enabled on the bar
- a `theme-set` hook, so theme switches repaint the lights
- systemd user units that restore lighting at login and after suspend

### Dependencies

| | Package | Why |
|---|---|---|
| Required | `python` | 3.11+, `tomllib` reads theme palettes |
| Recommended | `ttf-jetbrains-mono-nerd` | or the alien head renders as a blank box |
| Recommended | `alacritty` | a terminal for the KeyMap Wizard |
| Optional | `gum` | nicer installer prompts |

Omarchy itself, systemd, and `sudo` or `pkexec` for the udev step are also required.
Missing packages are installed with `omarchy pkg add`, falling back to `pacman`.

### Uninstall

```bash
./uninstall.sh
omarchy plugin remove 3mrgnc3.alienfx
```

Run `uninstall.sh` first. `omarchy plugin remove` deletes the plugin folder, which is all
Omarchy knows about, and the script lives in it.

## The popup

- **ThemeSync** on by default. Blends the active theme across every zone and repaints on
  theme change.
- **Brightness** and **Intensity** sliders.
- **ZoneSync** to drive all zones together, or off to set each one separately.
- **Colour pickers**, two of them, for the ends of a gradient range.
- **Effects**: Gradient, Wave, Pulse, Nightrider, Solid.
- **Profiles**: save and reload a whole setup by name.
- **KeyMap Wizard**, reachable from the keyboard icon at any time.

## The gradient

Only the keyboard is addressable per key, so it carries the real gradient. Each key's row
and column are normalised against the keymap and averaged for a top-left to bottom-right
diagonal.

Two things are less obvious. The blend holds near each anchor colour and crosses over
quickly through the middle, because key density along the diagonal peaks in the centre:
43 of 85 keys sit in the middle fifth of the range, so an even ramp spends most of its
surface on the intermediate hues. And interpolation runs through gamut-mapped OkLCh rather
than a straight line, which keeps chroma up instead of fading through grey.

The chassis zones take the exact colour of the corner key beside them. The touchpad matches
`esc`, the power button matches the bottom-right key.

Anchors come from the theme's `colors.toml`: the accent at one end, its most hue-distant
saturated colour at the other. A near-monochrome theme gets a synthesised complement rather
than a flat fill.

`docs/dead-ends.md` has the measurements and the things that didn't work.

### Intensity

Keycaps sit behind a diffuser that mixes white into everything, so a colour that looks
right on screen reads washed out on the keys. Intensity is an absolute saturation target
and the only saturation control.

| Slider | Saturation |
|---|---|
| -10 | 0.40, muted |
| 0 | 0.85, the default |
| +10 | 1.00 |

It applies in every mode. `alienfx-ctl set --intensity N`.

## CLI

Everything the popup does is available by hand, which makes it debuggable without the
shell running.

```bash
alienfx-ctl devices                    # what was found, and is it writable?
alienfx-ctl state                      # current settings
alienfx-ctl state --json               # what the plugin reads

alienfx-ctl solid --color ff7800 --brightness 10
alienfx-ctl solid --color '255,120,0' --zones kbd,logo
alienfx-ctl off --zones all
alienfx-ctl effect nightrider --color red --speed slow

alienfx-ctl theme show                 # anchors derived from the active theme
alienfx-ctl theme apply                # paint the theme gradient now
alienfx-ctl themesync on|off|status
alienfx-ctl zonesync on|off

alienfx-ctl profile list|save NAME|load NAME|rename OLD NEW|delete NAME
alienfx-ctl keymap status|show|import PATH|wizard
alienfx-ctl restore                    # what the systemd unit runs

alienfx-ctl set --color 00ff88 --zones logo --dry-run -v   # show, write nothing
```

Colours accept `RRGGBB`, `#RRGGBB`, `r,g,b`, a name, or `@themekey` to pull from the active
palette. Brightness accepts `0-255` or `10%`. Add `--persist` to write chassis colours into
NVRAM so they survive a cold boot.

## Why no `sudo`

The udev rule tags both HID nodes with `uaccess`, so logind grants the seat owner an ACL on
login. The CLI opens them as you.

The rule is numbered `60-` on purpose. `uaccess` is applied by a builtin that runs from
`73-seat-late.rules`, so a rule numbered above that is read too late and the tag is never
seen. An earlier version of this project spent a long time chasing what looked like a
boot-time race and was only ever a filename.

Nodes are resolved at runtime by vendor id and HID report shape, never by a fixed
`/dev/hidrawN`. On this laptop `hidraw0` is sometimes a security key.

## Other machines

The machine identifies itself from DMI and its keymap is stored per model, so a config
directory can move between machines without them fighting over one file.

Zones come from the keymap rather than a constant:

```json
"zones": { "tpd": [0], "logo": [2], "pbtn": [4] }
```

Any names work, and the order they're listed in is the order the gradient travels through
them. A keyboard lit as four zones rather than per key declares `"keyboard": "zones"` and
lists them the same way.

A keymap contributed for your model, bundled in `cli/src/alienfx_ctl/data/`, is used ahead
of the reference one. Your own probed keymap always wins over a bundled one.

`alienfx-ctl keymap gaps` reports LED indices the keymap doesn't name and flags those next
to a named key. Reach for it first if a single key behaves oddly.

## Known limits

- **The power button takes the slow path.** Ordinary colour commands are overridden by the
  firmware's power-state handler, so a colour only sticks by programming six NVRAM blocks.
  That's done, and the zone shows your colour on AC and battery, breathes while charging
  and fades going to sleep, while still warning in red when the battery is critical. It
  costs about 2.3s, so it's skipped during a live drag and when NVRAM already matches.
- **Chassis zones can't animate.** They're single-LED zones. Wave, Pulse and Nightrider are
  keyboard effects and the chassis holds the base colour.
- **Brightness is colour.** Neither controller exposes a brightness field on these paths, so
  brightness scales the RGB channels. At 10% a saturated colour is a dim ember.
- Effects that don't render usefully on the reference BIOS, such as hardware pulse, dual
  wave and laser, aren't offered rather than exposed and broken.

## Layout

```
manifest.json  Panel.qml  Model.js     the Quickshell plugin
cli/src/alienfx_ctl/                   the CLI
cli/src/alienfx_ctl/data/              reference keymap and keyboard shapes
cli/tests/                             551 tests, no hardware required
share/udev/                            the uaccess rule
share/systemd/                         restore and resume units
share/omarchy/hooks/theme-set.d/       the theme-switch hook
share/bin/                             the wizard's terminal launcher
docs/dead-ends.md                      what doesn't work, and why
```

Read `docs/dead-ends.md` before changing the hardware layer. It records failures that cost
real time, including two that brick the keyboard until you reboot.

Earlier versions of this tool are on the `archive-reference` branch rather than `main`, to
keep them out of every clone. Fetch with `git checkout archive-reference -- archive/`.

## Development

Checks run locally. There's no CI and no GitHub Actions.

```bash
cd cli && python3 -m pytest tests -q     # 551 tests, no hardware needed
```

That covers the protocol buffers, the gradient maths, the wizard, and the packaging checks:
the manifest against what Omarchy's plugin registry enforces, declared settings against the
ones the QML reads, the bundled layouts, and the shell scripts parsing.

## Licence

MIT. See [LICENSE](LICENSE).
