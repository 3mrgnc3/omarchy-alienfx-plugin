#!/bin/bash
# Install the Omarchy AlienFX plugin.
#
# Idempotent: safe to re-run to upgrade in place. Everything it touches is
# listed in uninstall.sh.
#
# Why an installer at all, when Omarchy can add a plugin straight from git?
# Because `omarchy plugin add` installs only the QML. The udev rule that makes
# the hardware reachable without sudo, the CLI that actually drives it, and the
# systemd unit that restores lighting at login all live outside the plugin
# folder. Without them the widget loads and reports "CLI NOT FOUND".

set -euo pipefail

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

say() { printf '  %s\n' "$*"; }
step() { printf '\n==> %s\n' "$*"; }

# Prefer sudo when a terminal can prompt; fall back to pkexec for a GUI launch.
as_root() {
  if [[ $EUID -eq 0 ]]; then "$@"
  elif sudo -n true 2>/dev/null; then sudo "$@"
  elif [[ -t 0 ]]; then sudo "$@"
  elif command -v pkexec >/dev/null 2>&1; then pkexec "$@"
  else
    echo "need root to install the udev rule; re-run from a terminal" >&2
    return 1
  fi
}

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

step "Installing the udev rule (needs root once)"
# The 60- prefix matters: the ACL is applied by the uaccess builtin invoked
# from 73-seat-late.rules, which only sees tags already set when it runs.
if as_root install -Dm644 "$REPO_DIR/share/udev/$UDEV_RULE" "/etc/udev/rules.d/$UDEV_RULE"; then
  as_root udevadm control --reload-rules || true
  as_root udevadm trigger --subsystem-match=hidraw --action=add || true
  say "rule -> /etc/udev/rules.d/$UDEV_RULE"
else
  say "SKIPPED - install it by hand, or the CLI will need sudo"
fi

step "Installing the Quickshell plugin"
mkdir -p "$PLUGIN_DIR"
install -Dm644 "$REPO_DIR/manifest.json" "$PLUGIN_DIR/manifest.json"
install -Dm644 "$REPO_DIR/Panel.qml" "$PLUGIN_DIR/Panel.qml"
install -Dm644 "$REPO_DIR/Model.js" "$PLUGIN_DIR/Model.js"
say "plugin -> $PLUGIN_DIR"

step "Installing the theme-set hook"
# A drop-in rather than the top-level hook file, so we never clobber a hook the
# user wrote themselves. Omarchy runs hooks/<name> and hooks/<name>.d/* both.
install -Dm755 "$REPO_DIR/share/omarchy/hooks/theme-set.d/50-omarchy-alienfx" \
  "$HOOK_DIR/50-omarchy-alienfx"
say "hook -> $HOOK_DIR/50-omarchy-alienfx"

step "Installing the systemd --user units"
install -Dm644 "$REPO_DIR/share/systemd/omarchy-alienfx-restore.service" "$UNIT_DIR/omarchy-alienfx-restore.service"
install -Dm644 "$REPO_DIR/share/systemd/omarchy-alienfx-resume.service" "$UNIT_DIR/omarchy-alienfx-resume.service"
systemctl --user daemon-reload || true
systemctl --user enable omarchy-alienfx-restore.service >/dev/null 2>&1 || say "could not enable restore unit"
systemctl --user enable omarchy-alienfx-resume.service >/dev/null 2>&1 || say "could not enable resume unit"
say "units -> $UNIT_DIR (enabled)"

step "Seeding configuration"
mkdir -p "$CONFIG_DIR/profiles" "$CONFIG_DIR/keymap"
# ThemeSync on by default, as the plugin's shipped behaviour.
"$BIN_DIR/alienfx-ctl" themesync on --no-save >/dev/null 2>&1 || true
if [[ ! -f $CONFIG_DIR/profiles/Default.json ]]; then
  "$BIN_DIR/alienfx-ctl" profile save Default >/dev/null 2>&1 && say "seeded the Default profile" \
    || say "could not seed Default yet (hardware not ready?) - the plugin can save it later"
else
  say "Default profile already present"
fi

step "Enabling the widget on the bar"
if omarchy plugin enable "$PLUGIN_ID" --section "$BAR_SECTION" >/dev/null 2>&1; then
  say "enabled in the '$BAR_SECTION' section"
else
  say "already enabled, or add it by hand: omarchy plugin enable $PLUGIN_ID --section $BAR_SECTION"
fi

step "Restarting the Omarchy shell"
omarchy restart shell >/dev/null 2>&1 || omarchy-restart-shell >/dev/null 2>&1 || say "restart the shell yourself"

cat <<DONE

Done. Look for the alien head in the '$BAR_SECTION' section of the bar.

Check it over:
  alienfx-ctl devices          # both controllers found and writable?
  alienfx-ctl state            # current settings
  alienfx-ctl theme show       # gradient anchors from the active theme

If PATH does not yet include ~/.local/bin, open a new shell first.
Uninstall with ./uninstall.sh
DONE
