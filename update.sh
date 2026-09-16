#!/bin/bash
# Update the plugin to the newest release.
#
# Reached from the popup: the version tag glows when a newer release is tagged,
# clicking it asks "Update now?", and yes opens a terminal running this.
# `alienfx-ctl update` does the same thing from a shell.
#
# Two commands, because `omarchy plugin update` only knows about the plugin
# folder. The CLI, the udev rule, the user services and the theme hook all live
# outside it and would otherwise stay at the old version - a half-update, with
# new QML calling an old CLI, which is a worse state than not updating at all.
#
#   1. omarchy plugin update - fast-forwards the checkout, after showing the
#      diff and asking. Omarchy validates the result and rolls back on its own
#      if the new version fails validation.
#   2. install.sh - refreshes everything outside the folder. Idempotent, and it
#      leaves the udev rule alone when it is already correct, so the usual
#      update needs no password at all.
#
# NOT RUNNABLE FROM THE PLUGIN FOLDER. Step 1 rewrites the files in that folder
# while this script is running, and bash reads a script incrementally as it
# executes: the merge would pull the ground out from under the interpreter
# mid-run. `alienfx-ctl update` copies this to a temporary file and runs that
# copy, which is why it exists rather than the popup launching this directly.

set -uo pipefail

PLUGIN_ID="3mrgnc3.alienfx"
PLUGIN_DIR="$HOME/.config/omarchy/plugins/$PLUGIN_ID"

ASSUME_YES=0
HOLD=0

for arg in "$@"; do
  case "$arg" in
    -y|--yes) ASSUME_YES=1 ;;
    --hold)   HOLD=1 ;;
    -h|--help)
      cat <<USAGE
Usage: alienfx-ctl update [--yes] [--hold]

Fast-forwards the plugin to the newest release and refreshes the parts that
live outside the plugin folder. You are shown the changes and asked before
anything is applied.

  --yes   do not ask; apply the update and refresh without confirmation
  --hold  wait for Enter before exiting, so a terminal opened purely to run
          this does not close before the result can be read
USAGE
      exit 0 ;;
    *) echo "unknown option: $arg (try --help)" >&2; exit 2 ;;
  esac
done

say()  { printf '  %s\n' "$*"; }
step() { printf '\n==> %s\n' "$*"; }
warn() { printf '  ! %s\n' "$*"; }
have() { command -v "$1" >/dev/null 2>&1; }

finish() {
  if (( HOLD )) && [[ -t 0 ]]; then
    printf '\n[press Enter to close] '
    read -r _
  fi
  exit "${1:-0}"
}

# ------------------------------------------------------------------ preflight

if ! have omarchy; then
  warn "omarchy is not on PATH, so there is nothing to update through"
  finish 1
fi

if [[ ! -d $PLUGIN_DIR/.git ]]; then
  warn "$PLUGIN_DIR is not a git checkout, so it cannot be updated in place."
  warn "This happens when the plugin was copied in by hand rather than added"
  warn "with 'omarchy plugin add'. Re-add it to get updates:"
  warn "  omarchy plugin remove $PLUGIN_ID"
  warn "  omarchy plugin add https://github.com/3mrgnc3/omarchy-alienfx-plugin.git"
  finish 1
fi

if [[ -n $(git -C "$PLUGIN_DIR" status --porcelain 2>/dev/null) ]]; then
  warn "there are local changes in $PLUGIN_DIR, so it cannot be fast-forwarded."
  warn "Commit, stash or discard them first. To see them:"
  warn "  git -C $PLUGIN_DIR status"
  finish 1
fi

cat <<INFO

  ==> Updating the AlienFX plugin

  Installed:  $(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["version"])' "$PLUGIN_DIR/manifest.json" 2>/dev/null || echo unknown)

  This does two things:

  1. omarchy plugin update $PLUGIN_ID
     Fast-forwards $PLUGIN_DIR.
     You will see the full diff and be asked before it is applied. Omarchy
     validates the result and rolls back by itself if it fails.

  2. $PLUGIN_DIR/install.sh
     Refreshes the parts outside the plugin folder so they match: the CLI,
     the user services and the theme hook. The udev rule is left alone
     unless it has actually changed, so this usually needs no password.

  Your settings, profiles and keymaps are not touched.

INFO

if (( ! ASSUME_YES )); then
  if ! [[ -t 0 ]]; then
    warn "not interactive and --yes was not given; nothing changed"
    finish 1
  fi
  if have gum; then
    gum confirm "Update now?" || { say "Nothing changed."; finish 0; }
  else
    read -r -p "  Update now? [y/N] " reply
    [[ ${reply,,} == y || ${reply,,} == yes ]] || { say "Nothing changed."; finish 0; }
  fi
fi

# ------------------------------------------------------------------- the work

step "Fetching and applying"
BEFORE="$(git -C "$PLUGIN_DIR" rev-parse HEAD 2>/dev/null)"

update_args=("$PLUGIN_ID")
(( ASSUME_YES )) && update_args+=("--yes")

if ! omarchy plugin update "${update_args[@]}"; then
  warn "omarchy plugin update did not complete; nothing outside the plugin"
  warn "folder has been touched."
  finish 1
fi

AFTER="$(git -C "$PLUGIN_DIR" rev-parse HEAD 2>/dev/null)"

if [[ $BEFORE == "$AFTER" ]]; then
  step "Nothing to do"
  say "Already at the newest version, or the update was declined."
  finish 0
fi

step "Refreshing the parts outside the plugin folder"
if [[ ! -x $PLUGIN_DIR/install.sh ]]; then
  warn "install.sh is missing from the updated plugin; the QML is new but the"
  warn "CLI is still the old one. Fix with:"
  warn "  bash $PLUGIN_DIR/install.sh"
  finish 1
fi

# --no-deps: dependencies were resolved at install time and an update is not
# the moment to start installing packages behind someone's back.
if ! "$PLUGIN_DIR/install.sh" --yes --no-deps; then
  warn "the plugin folder was updated but the refresh failed. The popup may be"
  warn "newer than the CLI it calls. Re-run:"
  warn "  $PLUGIN_DIR/install.sh"
  finish 1
fi

step "Done"
say "Updated to $(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["version"])' "$PLUGIN_DIR/manifest.json" 2>/dev/null || echo 'the newest version')."
say "The popup's version tag will stop glowing next time you open it."
finish 0
