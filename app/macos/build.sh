#!/bin/bash
# Builds "OS3 Router.app" (menu bar app) into app/macos/build/. Ad-hoc signed: fine for the
# curl installer (no quarantine flag); a downloadable .dmg needs a Developer ID + notarization.
set -euo pipefail
cd "$(dirname "$0")"
# the app is a thin window around the router's own page: its version only changes when the app
# itself changes, so router updates don't replace it (every new unsigned build needs approving again)
VERSION=$(cat VERSION)
ARCH="--arch arm64 --arch x86_64"  # universal needs full Xcode; Command Line Tools build this Mac's arch only
swift build -c release $ARCH >/dev/null 2>&1 || { ARCH=""; swift build -c release >/dev/null; }
BIN=$(swift build -c release $ARCH --show-bin-path)/CodexOS3
APP="build/OS3 Router.app"
rm -rf "$APP" && mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BIN" "$APP/Contents/MacOS/CodexOS3"
cp AppIcon.icns "$APP/Contents/Resources/AppIcon.icns"
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>OS3 Router</string>
  <key>CFBundleDisplayName</key><string>OS3 Router</string>
  <key>CFBundleIdentifier</key><string>ai.os3router.app</string>
  <key>CFBundleExecutable</key><string>CodexOS3</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundleShortVersionString</key><string>${VERSION}</string>
  <key>CFBundleVersion</key><string>${VERSION}</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>LSUIElement</key><true/>
  <key>NSAppTransportSecurity</key><dict><key>NSAllowsLocalNetworking</key><true/></dict>
</dict></plist>
PLIST
codesign --force --deep -s - "$APP"
echo "$APP"
