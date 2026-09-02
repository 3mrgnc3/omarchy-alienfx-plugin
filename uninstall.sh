#!/bin/bash
# Remove the Omarchy AlienFX plugin.
#
# Keeps your data by default: profiles, keymap and current state stay in
# ~/.config/omarchy-alienfx-plugin. Pass --purge to remove those too.
#
# Run this BEFORE `omarchy plugin remove mrgnc.alienfx`. That command deletes
# the plugin folder, which is all Omarchy knows about - the CLI, the udev rule
# and the systemd units live outside it and would be left behind.
#
# Safe to run from either a repo clone or from inside the plugin folder itself.

set -uo pipefail

PLUGIN_ID="mrgnc.alienfx"
SELF_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

SHARE_DIR="$HOME/.local/share/omarchy-alienfx-plugin"
BIN_DIR="$HOME/.local/bin"
PLUGIN_DIR="$HOME/.config/omarchy/plugins/$PLUGIN_ID"
UNIT_DIR="$HOME/.config/systemd/user"
HOOK="$HOME/.config/omarchy/hooks/theme-set.d/50-omarchy-alienfx"
UDEV_RULE="/etc/udev/rules.d/60-omarchy-alienfx.rules"
CONFIG_DIR="$HOME/.config/omarchy-alienfx-plugin"

PURGE=0
ASSUME_YES=0
for arg in "$@"; do
  case "$arg" in
    --purge)  PURGE=1 ;;
    -y|--yes) ASSUME_YES=1 ;;
    -h|--help)
      echo "Usage: ./uninstall.sh [--purge] [--yes]"
      echo "  --purge   also delete profiles, keymap and saved state"
      echo "  --yes     do not ask for confirmation"
      exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

say()  { printf '  %s\n' "$*"; }
step() { printf '\n==> %s\n' "$*"; }
warn() { printf '  ! %s\n' "$*"; }
have() { command -v "$1" >/dev/null 2>&1; }

as_root() {
  if   [[ $EUID -eq 0 ]];        then "$@"
  elif sudo -n true 2>/dev/null; then sudo "$@"
  elif [[ -t 0 ]];               then sudo "$@"
  elif have pkexec;              then pkexec "$@"
  else return 1
  fi
}

if (( ! ASSUME_YES )) && [[ -t 0 ]]; then
  if have gum; then
    gum confirm "Remove the AlienFX plugin?" || { say "aborted"; exit 1; }
  else
    read -r -p "  Remove the AlienFX plugin? [y/N] " reply
    [[ ${reply,,} == y || ${reply,,} == yes ]] || { say "aborted"; exit 1; }
  fi
fi

# Resolve a CLI to turn the lights off with, wherever it happens to live.
CLI=""
for candidate in "$BIN_DIR/alienfx-ctl" "$SELF_DIR/cli/bin/alienfx-ctl" "$PLUGIN_DIR/cli/bin/alienfx-ctl"; do
  [[ -x $candidate ]] && { CLI="$candidate"; break; }
done

step "Turning the lights off"
if [[ -n $CLI ]] && "$CLI" off --zones all >/dev/null 2>&1; then
  say "all zones off"
else
  warn "could not reach the hardware; the lights may stay as they are"
fi

step "Disabling the widget"
if omarchy plugin disable "$PLUGIN_ID" >/dev/null 2>&1; then
  say "disabled"
else
  say "was not enabled"
fi

step "Removing the systemd units"
for unit in omarchy-alienfx-restore omarchy-alienfx-resume; do
  systemctl --user disable --now "$unit.service" >/dev/null 2>&1
  if [[ -f "$UNIT_DIR/$unit.service" ]]; then
    rm -f "$UNIT_DIR/$unit.service" && say "removed $unit.service"
  fi
done
systemctl --user daemon-reload >/dev/null 2>&1

step "Removing files"
[[ -f $HOOK ]] && rm -f "$HOOK" && say "removed the theme-set hook"
[[ -d $SHARE_DIR ]] && rm -rf "$SHARE_DIR" && say "removed $SHARE_DIR"
for launcher in alienfx-ctl omarchy-alienfx-wizard; do
  [[ -e "$BIN_DIR/$launcher" ]] && rm -f "$BIN_DIR/$launcher" && say "removed $BIN_DIR/$launcher"
done

if [[ -f $UDEV_RULE ]]; then
  if as_root rm -f "$UDEV_RULE"; then
    as_root udevadm control --reload-rules >/dev/null 2>&1
    say "removed the udev rule"
  else
    warn "could not remove $UDEV_RULE (needs root) - remove it by hand"
  fi
fi

step "Removing the plugin folder"
# If this script is running from inside the plugin folder - which is where
# `omarchy plugin add` put it - deleting that folder out from under a running
# bash is asking for trouble. Hand it to Omarchy instead.
if [[ "$(readlink -f "$SELF_DIR")" == "$(readlink -f "$PLUGIN_DIR")" ]]; then
  say "running from inside it, so leaving it to Omarchy. Finish with:"
  say "    omarchy plugin remove $PLUGIN_ID"
elif [[ -d $PLUGIN_DIR ]]; then
  if omarchy plugin remove "$PLUGIN_ID" --yes >/dev/null 2>&1; then
    say "removed via omarchy plugin remove"
  else
    rm -rf "$PLUGIN_DIR" && say "removed $PLUGIN_DIR"
  fi
else
  say "already gone"
fi

step "User data"
if (( PURGE )); then
  [[ -d $CONFIG_DIR ]] && rm -rf "$CONFIG_DIR" && say "purged $CONFIG_DIR"
else
  say "kept your profiles and keymap in $CONFIG_DIR"
  say "(re-run with --purge to remove them as well)"
fi

step "Restarting the Omarchy shell"
omarchy restart shell >/dev/null 2>&1 || omarchy-restart-shell >/dev/null 2>&1 || warn "restart the shell yourself"
printf '\nDone.\n'
