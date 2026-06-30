#!/usr/bin/env bash
# Build a macOS .dmg installer for Mynah — a styled disk image with the app icon next to an
# Applications-folder shortcut, so a user drags Mynah across (the conventional macOS install
# experience the .zip can't give).
#
# Primary path: `create-dmg` (Homebrew) for the styled window. Fallback: a hand-staged folder
# (Mynah.app + a symlink to /Applications) packed with `hdiutil` — no background art, but still
# the drag-to-Applications layout — so a flaky create-dmg AppleScript step can never fail the
# release.
#
# Usage:  scripts/make_dmg.sh <path-to-Mynah.app> <output.dmg> [volume-name]

set -euo pipefail

APP="${1:?usage: make_dmg.sh <Mynah.app> <output.dmg> [volname]}"
OUT="${2:?usage: make_dmg.sh <Mynah.app> <output.dmg> [volname]}"
VOLNAME="${3:-Mynah}"

if [[ ! -d "$APP" ]]; then
  echo "ERROR: app bundle not found: $APP" >&2
  exit 1
fi
mkdir -p "$(dirname "$OUT")"
rm -f "$OUT"

make_with_create_dmg() {
  command -v create-dmg >/dev/null 2>&1 || return 1
  echo "make_dmg: building styled DMG with create-dmg…"
  # create-dmg occasionally returns non-zero from a cosmetic AppleScript step even when the .dmg
  # is written fine, so treat "file exists afterwards" as the real success signal.
  create-dmg \
    --volname "$VOLNAME" \
    --window-pos 200 120 \
    --window-size 540 380 \
    --icon-size 110 \
    --icon "$(basename "$APP")" 150 190 \
    --app-drop-link 390 190 \
    --hdiutil-quiet \
    "$OUT" "$APP" || true
  [[ -f "$OUT" ]]
}

make_with_hdiutil() {
  echo "make_dmg: falling back to hdiutil (plain drag-to-Applications layout)…"
  local stage
  stage="$(mktemp -d)"
  cp -R "$APP" "$stage/"
  ln -s /Applications "$stage/Applications"
  hdiutil create -volname "$VOLNAME" -srcfolder "$stage" -ov -format UDZO "$OUT"
  rm -rf "$stage"
  [[ -f "$OUT" ]]
}

if make_with_create_dmg; then
  echo "make_dmg: wrote $OUT (create-dmg)"
elif make_with_hdiutil; then
  echo "make_dmg: wrote $OUT (hdiutil fallback)"
else
  echo "ERROR: could not build $OUT by any method" >&2
  exit 1
fi
