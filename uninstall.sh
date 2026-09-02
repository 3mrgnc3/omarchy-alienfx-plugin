#!/bin/bash
# Remove the Omarchy AlienFX plugin.
#
# Keeps your data by default: profiles, keymap and current state stay in
# ~/.config/omarchy-alienfx-plugin. Pass --purge to remove those too.

set -uo pipefail

PLUGIN_ID="mrgnc.alienfx"
SHARE_DIR="$HOME/.local/share/omarchy-alienfx-plugin"
BIN_DIR="$HOME/.local/bin"
PLUGIN_DIR="$HOME/.config/omarchy/plugins/$PLUGIN_ID"
UNIT_DIR="$HOME/.config/systemd/user"
HOOK="$HOME/.config/omarchy/hooks/theme-set.d/50-omarchy-alienfx"
UDEV_RULE="/etc/udev/rules.d/60-omarchy-alienfx.rules"
CONFIG_DIR="$HOME/.config/omarchy-alienfx-plugin"

PURGE=0
[[ ${1:-} == --purge ]] && PURGE=1

say() { printf '  %s\n' "$*"; }
as_root() {
  if [[ $EUID -eq 0 ]]; then "$@"
  elif sudo -n true 2>/dev/null; then sudo "$@"
  elif [[ -t 0 ]]; then sudo "$@"
  elif command -v pkexec >/dev/null 2>&1; then pkexec "$@"
  else return 1; fi
}

printf '\n==> Turning the lights off\n'
"$BIN_DIR/alienfx-ctl" off --zones all >/dev/null 2>&1 && say "all zones off" || say "could not reach the hardware"

printf '\n==> Disabling the widget\n'
omarchy plugin disable "$PLUGIN_ID" >/dev/null 2>&1 && say "disabled" || say "was not enabled"

printf '\n==> Removing units\n'
for unit in omarchy-alienfx-restore omarchy-alienfx-resume; do
  systemctl --user disable --now "$unit.service" >/dev/null 2>&1
  rm -f "$UNIT_DIR/$unit.service" && say "removed $unit.service"
done
systemctl --user daemon-reload >/dev/null 2>&1

printf '\n==> Removing files\n'
rm -f "$HOOK" && say "removed the theme-set hook"
rm -rf "$PLUGIN_DIR" && say "removed $PLUGIN_DIR"
rm -rf "$SHARE_DIR" && say "removed $SHARE_DIR"
rm -f "$BIN_DIR/alienfx-ctl" "$BIN_DIR/omarchy-alienfx-wizard" && say "removed the launchers"
if [[ -f $UDEV_RULE ]]; then
  as_root rm -f "$UDEV_RULE" && as_root udevadm control --reload-rules && say "removed the udev rule" \
    || say "could not remove $UDEV_RULE (needs root)"
fi

if (( PURGE )); then
  rm -rf "$CONFIG_DIR" && say "purged $CONFIG_DIR"
else
  say "kept your profiles and keymap in $CONFIG_DIR (--purge to remove)"
fi

printf '\n==> Restarting the shell\n'
omarchy restart shell >/dev/null 2>&1 || omarchy-restart-shell >/dev/null 2>&1 || say "restart the shell yourself"
printf '\nDone.\n'
