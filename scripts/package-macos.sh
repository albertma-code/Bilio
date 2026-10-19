#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="$ROOT/src-tauri/target/release/bundle/macos/Bilio.app"
/usr/bin/codesign --force --deep --sign - --timestamp=none "$APP"
/usr/bin/codesign --verify --deep --strict "$APP"
VERSION="$(/usr/bin/plutil -extract CFBundleShortVersionString raw "$APP/Contents/Info.plist")"
OUT="$ROOT/src-tauri/target/release/bundle/dmg/Bilio_${VERSION}_aarch64.dmg"
STAGING="$(mktemp -d "${TMPDIR:-/tmp}/bilio-dmg.XXXXXX")"
trap 'rm -rf "$STAGING"' EXIT
mkdir -p "$(dirname "$OUT")"
/usr/bin/ditto "$APP" "$STAGING/Bilio.app"
ln -s /Applications "$STAGING/Applications"
/usr/bin/hdiutil create -volname Bilio -srcfolder "$STAGING" -ov -format UDZO "$OUT"
/usr/bin/hdiutil verify "$OUT"
echo "Verified disk image: $OUT"
