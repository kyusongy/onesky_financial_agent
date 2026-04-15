#!/usr/bin/env bash
# Build, sign, notarize, and zip the OneSky Financial Agent desktop .app.
# Requires: APPLE_ID, APPLE_PASSWORD, APPLE_TEAM_ID in the environment (see .env).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$SCRIPT_DIR/.."
APP_NAME="OneSky Financial Agent"
APP_PATH="$ROOT/dist/$APP_NAME.app"
ZIP_PATH="$ROOT/dist/$APP_NAME.zip"
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

echo "==> Codesigning with hardened runtime"
# Sign all nested binaries first (deep pass)
codesign --force --deep --options runtime \
    --entitlements "$ENTITLEMENTS" \
    --sign "$SIGN_IDENTITY" \
    --timestamp \
    "$APP_PATH"

echo "==> Verifying signature"
codesign --verify --deep --strict --verbose=2 "$APP_PATH"

echo "==> Zipping for notarization"
NOTARIZE_ZIP="$ROOT/dist/notarize.zip"
/usr/bin/ditto -c -k --keepParent "$APP_PATH" "$NOTARIZE_ZIP"

echo "==> Submitting to Apple for notarization (this can take several minutes)"
xcrun notarytool submit "$NOTARIZE_ZIP" \
    --apple-id "$APPLE_ID" \
    --password "$APPLE_PASSWORD" \
    --team-id "$APPLE_TEAM_ID" \
    --wait

echo "==> Stapling notarization ticket"
xcrun stapler staple "$APP_PATH"
xcrun stapler validate "$APP_PATH"

echo "==> Creating distribution zip"
rm -f "$ZIP_PATH" "$NOTARIZE_ZIP"
/usr/bin/ditto -c -k --keepParent "$APP_PATH" "$ZIP_PATH"

echo ""
echo "✓ Build complete: $ZIP_PATH"
du -sh "$ZIP_PATH" "$APP_PATH"
