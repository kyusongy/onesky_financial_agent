#!/usr/bin/env bash
# Build, sign, notarize, and package the OneSky Financial Agent desktop app
# as a signed/notarized .dmg for distribution.
#
# Requires: APPLE_ID, APPLE_PASSWORD, APPLE_TEAM_ID in the environment.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$SCRIPT_DIR/.."
APP_NAME="OneSky Financial Agent"
APP_PATH="$ROOT/dist/$APP_NAME.app"
DMG_PATH="$ROOT/dist/$APP_NAME.dmg"
DMG_STAGE="$ROOT/dist/dmg_stage"
SIGN_IDENTITY="Developer ID Application: SHIPIAN HUANG (SP9KQ7NFJN)"
ENTITLEMENTS="$SCRIPT_DIR/entitlements.plist"

: "${APPLE_ID:?APPLE_ID not set}"
: "${APPLE_PASSWORD:?APPLE_PASSWORD not set}"
: "${APPLE_TEAM_ID:?APPLE_TEAM_ID not set}"

cd "$ROOT"

echo "==> Cleaning previous builds"
rm -rf build dist

echo "==> Ensuring build deps"
.venv/bin/pip install -q pyinstaller pillow

if [ ! -f "$SCRIPT_DIR/icon.icns" ]; then
    echo "==> Generating icon"
    .venv/bin/python "$SCRIPT_DIR/gen_icon.py"
fi

echo "==> Running PyInstaller"
.venv/bin/pyinstaller onesky.spec --noconfirm --clean

echo "==> Codesigning .app with hardened runtime"
codesign --force --deep --options runtime \
    --entitlements "$ENTITLEMENTS" \
    --sign "$SIGN_IDENTITY" \
    --timestamp \
    "$APP_PATH"

echo "==> Verifying .app signature"
codesign --verify --deep --strict --verbose=2 "$APP_PATH"

echo "==> Notarizing .app"
APP_NOTARIZE_ZIP="$ROOT/dist/app-notarize.zip"
/usr/bin/ditto -c -k --keepParent "$APP_PATH" "$APP_NOTARIZE_ZIP"
xcrun notarytool submit "$APP_NOTARIZE_ZIP" \
    --apple-id "$APPLE_ID" \
    --password "$APPLE_PASSWORD" \
    --team-id "$APPLE_TEAM_ID" \
    --wait
xcrun stapler staple "$APP_PATH"
xcrun stapler validate "$APP_PATH"
rm -f "$APP_NOTARIZE_ZIP"

echo "==> Building DMG"
rm -rf "$DMG_STAGE"
mkdir -p "$DMG_STAGE"
cp -R "$APP_PATH" "$DMG_STAGE/"
ln -s /Applications "$DMG_STAGE/Applications"
rm -f "$DMG_PATH"
hdiutil create \
    -volname "$APP_NAME" \
    -srcfolder "$DMG_STAGE" \
    -ov \
    -format UDZO \
    "$DMG_PATH"
rm -rf "$DMG_STAGE"

echo "==> Signing DMG"
codesign --force --sign "$SIGN_IDENTITY" --timestamp "$DMG_PATH"

echo "==> Notarizing DMG"
xcrun notarytool submit "$DMG_PATH" \
    --apple-id "$APPLE_ID" \
    --password "$APPLE_PASSWORD" \
    --team-id "$APPLE_TEAM_ID" \
    --wait

echo "==> Stapling DMG"
xcrun stapler staple "$DMG_PATH"
xcrun stapler validate "$DMG_PATH"

echo ""
echo "✓ Build complete: $DMG_PATH"
du -sh "$DMG_PATH" "$APP_PATH"
