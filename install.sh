#!/bin/bash
# Install the Omarchy AlienFX plugin.
#
# Idempotent: safe to re-run to upgrade in place. Everything it touches is
# listed in uninstall.sh.
#
# Works two ways:
#   1. from a clone:  git clone ... && cd ... && ./install.sh
#   2. from the plugin dir, after `omarchy plugin add <url>` cloned the repo
#      into ~/.config/omarchy/plugins/mrgnc.alienfx/ - the popup's
#      "Complete setup" button runs it from there.
#
# `omarchy plugin add` installs only the QML. The udev rule that makes the
# hardware reachable without sudo, the CLI that drives it, and the systemd unit
# that restores lighting at login all live outside the plugin folder, so this
# script is what finishes the job.

set -uo pipefail

REPO_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ID="mrgnc.alienfx"

SHARE_DIR="$HOME/.local/share/omarchy-alienfx-plugin"
BIN_DIR="$HOME/.local/bin"
PLUGIN_DIR="$HOME/.config/omarchy/plugins/$PLUGIN_ID"
UNIT_DIR="$HOME/.config/systemd/user"
HOOK_DIR="$HOME/.config/omarchy/hooks/theme-set.d"
UDEV_RULE="60-omarchy-alienfx.rules"
CONFIG_DIR="$HOME/.config/omarchy-alienfx-plugin"

BAR_SECTION="${ALIENFX_BAR_SECTION:-right}"
ASSUME_YES=0
SKIP_DEPS=0

for arg in "$@"; do
  case "$arg" in
    -y|--yes)  ASSUME_YES=1 ;;
    --no-deps) SKIP_DEPS=1 ;;
    -h|--help)
      cat <<USAGE
Usage: ./install.sh [--yes] [--no-deps]

  --yes       non-interactive: install any missing dependencies without asking
  --no-deps   do not install anything; report missing dependencies and continue
USAGE
      exit 0 ;;
    *) echo "unknown option: $arg (try --help)" >&2; exit 2 ;;
  esac
done

say()  { printf '  %s\n' "$*"; }
step() { printf '\n==> %s\n' "$*"; }
warn() { printf '  ! %s\n' "$*"; }

have() { command -v "$1" >/dev/null 2>&1; }

# sudo when a terminal can prompt; pkexec for a GUI launch with no tty.
as_root() {
  if   [[ $EUID -eq 0 ]];            then "$@"
  elif sudo -n true 2>/dev/null;     then sudo "$@"
  elif [[ -t 0 ]];                   then sudo "$@"
  elif have pkexec;                  then pkexec "$@"
  else return 1
  fi
}

ask() {
  # ask "<question>" -> 0 for yes, 1 for no. Uses gum when Omarchy has it.
  local question="$1"
  (( ASSUME_YES )) && return 0
  if ! [[ -t 0 ]]; then
    warn "not interactive and --yes was not given; assuming no"
    return 1
  fi
  if have gum; then
    gum confirm "$question" && return 0 || return 1
  fi
  local reply
  read -r -p "  $question [y/N] " reply
  [[ ${reply,,} == y || ${reply,,} == yes ]]
}

# ----------------------------------------------------------------- preflight
#
# Report everything missing up front, then let the user decide once, rather
# than failing halfway through and leaving a half-installed system - which is
# exactly the state the previous generation of this tool was found in.

declare -a HARD_MISSING=() SOFT_MISSING=() PKGS=()

preflight() {
  step "Checking dependencies"

  # --- hard: the plugin cannot work without these ---
  if ! have omarchy; then
    HARD_MISSING+=("omarchy - this is an Omarchy plugin; install Omarchy first (omarchy.org)")
  fi

  if ! have python3; then
    HARD_MISSING+=("python3 - the CLI is written in Python")
    PKGS+=("python")
  elif ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
    HARD_MISSING+=("python3 >= 3.11 - found $(python3 -V 2>&1 | awk '{print $2}'); tomllib is needed to read theme palettes")
    PKGS+=("python")
  fi

  if ! have systemctl || ! have udevadm; then
    HARD_MISSING+=("systemd (systemctl, udevadm) - needed for device access and login restore")
  fi

  if [[ $EUID -ne 0 ]] && ! have sudo && ! have pkexec; then
    HARD_MISSING+=("sudo or pkexec - the udev rule needs root once")
  fi

  # --- soft: it installs and runs, but something is degraded ---
  if ! fc-list 2>/dev/null | grep -qi 'nerd font'; then
    SOFT_MISSING+=("a Nerd Font - without one the alien head on the bar renders as a blank box")
    PKGS+=("ttf-jetbrains-mono-nerd")
  fi

  local have_term=0
  for t in ghostty kitty alacritty foot omarchy-launch-terminal omarchy-launch-or-focus-tui; do
    have "$t" && { have_term=1; break; }
  done
  if (( ! have_term )); then
    SOFT_MISSING+=("a terminal emulator - the KeyMap Wizard opens in one")
    PKGS+=("alacritty")
  fi

  if ! have gum; then
    SOFT_MISSING+=("gum - only used to make these prompts nicer")
  fi

  # --- report ---
  if (( ${#HARD_MISSING[@]} == 0 && ${#SOFT_MISSING[@]} == 0 )); then
    say "all dependencies present"
    return 0
  fi

  if (( ${#HARD_MISSING[@]} )); then
    printf '\n  REQUIRED, and missing:\n'
    printf '    - %s\n' "${HARD_MISSING[@]}"
  fi
  if (( ${#SOFT_MISSING[@]} )); then
    printf '\n  Recommended, and missing:\n'
    printf '    - %s\n' "${SOFT_MISSING[@]}"
  fi

  # Nothing we can install for them (e.g. Omarchy or systemd itself) is fatal.
  if (( ${#PKGS[@]} == 0 )); then
    if (( ${#HARD_MISSING[@]} )); then
      printf '\n'
      warn "these cannot be installed automatically. Aborting."
      exit 1
    fi
    printf '\n'
    ask "Continue anyway?" || { say "aborted"; exit 1; }
    return 0
  fi

  # Deduplicate the package list.
  mapfile -t PKGS < <(printf '%s\n' "${PKGS[@]}" | awk '!seen[$0]++')

  printf '\n  Installable with your package manager: %s\n\n' "${PKGS[*]}"

  if (( SKIP_DEPS )); then
    if (( ${#HARD_MISSING[@]} )); then
      warn "--no-deps given but required dependencies are missing. Aborting."
      exit 1
    fi
    warn "--no-deps given; continuing without them"
    return 0
  fi

  if ask "Install these packages now?"; then
    step "Installing packages"
    if have omarchy && omarchy pkg add "${PKGS[@]}"; then
      say "installed via omarchy pkg add"
    elif as_root pacman -S --needed --noconfirm "${PKGS[@]}"; then
      say "installed via pacman"
    else
      warn "package installation failed"
      if (( ${#HARD_MISSING[@]} )); then
        warn "required dependencies are still missing. Aborting."
        exit 1
      fi
      ask "Continue without them?" || { say "aborted"; exit 1; }
    fi
  else
    if (( ${#HARD_MISSING[@]} )); then
      say "aborted - required dependencies were declined"
      exit 1
    fi
    ask "Continue without the recommended packages?" || { say "aborted"; exit 1; }
  fi
}

preflight

# ------------------------------------------------------------------- install
step "Installing the alienfx-ctl CLI"
mkdir -p "$SHARE_DIR" "$BIN_DIR"
rm -rf "$SHARE_DIR/alienfx_ctl"
cp -a "$REPO_DIR/cli/src/alienfx_ctl" "$SHARE_DIR/alienfx_ctl"
say "package -> $SHARE_DIR/alienfx_ctl"

cat > "$BIN_DIR/alienfx-ctl" <<LAUNCHER
#!/usr/bin/env python3
"""Launcher for alienfx-ctl (written by install.sh)."""
import sys
sys.path.insert(0, "$SHARE_DIR")
from alienfx_ctl.cli import main
sys.exit(main())
LAUNCHER
chmod 0755 "$BIN_DIR/alienfx-ctl"
say "launcher -> $BIN_DIR/alienfx-ctl"

install -Dm755 "$REPO_DIR/share/bin/omarchy-alienfx-wizard" "$BIN_DIR/omarchy-alienfx-wizard"
say "wizard launcher -> $BIN_DIR/omarchy-alienfx-wizard"

if ! printf '%s' ":$PATH:" | grep -q ":$BIN_DIR:"; then
  warn "$BIN_DIR is not on your PATH - open a new shell before using alienfx-ctl by name"
fi

step "Installing the udev rule (needs root once)"
# The 60- prefix matters: the ACL is applied by the uaccess builtin invoked from
# 73-seat-late.rules, which only sees tags already set when it runs. A rule
# numbered above 73 never gets an ACL on the keyboard node.
if as_root install -Dm644 "$REPO_DIR/share/udev/$UDEV_RULE" "/etc/udev/rules.d/$UDEV_RULE"; then
  as_root udevadm control --reload-rules || true
  as_root udevadm trigger --subsystem-match=hidraw --action=add || true
  say "rule -> /etc/udev/rules.d/$UDEV_RULE"
else
  warn "could not install the udev rule; the CLI will need sudo until you do:"
  warn "  sudo install -m 0644 share/udev/$UDEV_RULE /etc/udev/rules.d/"
fi

step "Installing the Quickshell plugin"
if [[ "$(readlink -f "$REPO_DIR")" == "$(readlink -f "$PLUGIN_DIR")" ]]; then
  # Already running from inside the plugin directory, which is where
  # `omarchy plugin add` puts the clone. Nothing to copy.
  say "running from $PLUGIN_DIR - already in place"
else
  mkdir -p "$PLUGIN_DIR"
  install -Dm644 "$REPO_DIR/manifest.json" "$PLUGIN_DIR/manifest.json"
  install -Dm644 "$REPO_DIR/Panel.qml" "$PLUGIN_DIR/Panel.qml"
  install -Dm644 "$REPO_DIR/Model.js" "$PLUGIN_DIR/Model.js"
  say "plugin -> $PLUGIN_DIR"
fi

step "Installing the theme-set hook"
# A drop-in rather than the top-level hook file, so a hook the user wrote
# themselves is never clobbered. Omarchy runs hooks/<name> and hooks/<name>.d/*.
install -Dm755 "$REPO_DIR/share/omarchy/hooks/theme-set.d/50-omarchy-alienfx" \
  "$HOOK_DIR/50-omarchy-alienfx"
say "hook -> $HOOK_DIR/50-omarchy-alienfx"

step "Installing the systemd --user units"
install -Dm644 "$REPO_DIR/share/systemd/omarchy-alienfx-restore.service" "$UNIT_DIR/omarchy-alienfx-restore.service"
install -Dm644 "$REPO_DIR/share/systemd/omarchy-alienfx-resume.service" "$UNIT_DIR/omarchy-alienfx-resume.service"
systemctl --user daemon-reload || true
systemctl --user enable omarchy-alienfx-restore.service >/dev/null 2>&1 || warn "could not enable the restore unit"
systemctl --user enable omarchy-alienfx-resume.service >/dev/null 2>&1 || warn "could not enable the resume unit"
say "units -> $UNIT_DIR (enabled)"

step "Seeding configuration"
mkdir -p "$CONFIG_DIR/profiles" "$CONFIG_DIR/keymap"
"$BIN_DIR/alienfx-ctl" themesync on --no-save >/dev/null 2>&1 || true
if [[ ! -f $CONFIG_DIR/profiles/Default.json ]]; then
  if "$BIN_DIR/alienfx-ctl" profile save Default >/dev/null 2>&1; then
    say "seeded the Default profile (ThemeSync on)"
  else
    warn "could not seed Default yet - the plugin can save it once the hardware is reachable"
  fi
else
  say "Default profile already present"
fi

step "Enabling the widget on the bar"
if omarchy plugin enable "$PLUGIN_ID" --section "$BAR_SECTION" >/dev/null 2>&1; then
  say "enabled in the '$BAR_SECTION' section"
else
  say "already enabled (or: omarchy plugin enable $PLUGIN_ID --section $BAR_SECTION)"
fi

step "Restarting the Omarchy shell"
omarchy restart shell >/dev/null 2>&1 || omarchy-restart-shell >/dev/null 2>&1 || warn "restart the shell yourself"

step "Verifying"
if "$BIN_DIR/alienfx-ctl" devices 2>/dev/null | grep -q 'NOT writable'; then
  warn "a controller is not writable yet - reboot or replug, then: alienfx-ctl devices"
else
  say "both controllers found and writable"
fi

cat <<DONE

Done. Look for the alien head in the '$BAR_SECTION' section of the bar.

  alienfx-ctl devices      both controllers found and writable?
  alienfx-ctl state        current settings
  alienfx-ctl theme show   gradient anchors from the active theme

Uninstall with ./uninstall.sh
DONE
