#!/bin/bash
# Builds "Codex OS3.app" (menu bar app) into app/macos/build/. Ad-hoc signed: fine for the
# curl installer (no quarantine flag); a downloadable .dmg needs a Developer ID + notarization.
set -euo pipefail
cd "$(dirname "$0")"
VERSION=$(python3 -c "import sys; sys.path.insert(0, '../..'); import codex_os3; print(codex_os3.__version__)")
swift build -c release --arch arm64 --arch x86_64 >/dev/null
BIN=$(swift build -c release --arch arm64 --arch x86_64 --show-bin-path)/CodexOS3
APP="build/Codex OS3.app"
rm -rf "$APP" && mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BIN" "$APP/Contents/MacOS/CodexOS3"
cp AppIcon.icns "$APP/Contents/Resources/AppIcon.icns"
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Codex OS3</string>
  <key>CFBundleDisplayName</key><string>Codex OS3</string>
  <key>CFBundleIdentifier</key><string>ai.codexos3.menubar</string>
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
