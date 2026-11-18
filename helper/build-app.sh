#!/bin/bash
# Builds "Pad Deck Helper.app" (Apple silicon + Intel) and a zip of it for a GitHub release:
#   helper/build-app.sh        → dist/Pad Deck Helper.app, dist/Pad-Deck-Helper-Mac.zip
# Needs Xcode or Apple's Command Line Tools. The app is signed ad hoc (not notarized).
set -euo pipefail
cd "$(dirname "$0")/.."
OUT=dist
APP="$OUT/Pad Deck Helper.app"
VERSION="${1:-1.0}"
# swiftc and the SDK are used directly: xcrun fails in a shell running under Rosetta.
DEV=$(xcode-select -p)
SWIFTC="$DEV/usr/bin/swiftc"; [ -x "$SWIFTC" ] || SWIFTC="$DEV/Toolchains/XcodeDefault.xctoolchain/usr/bin/swiftc"
SDK="$DEV/SDKs/MacOSX.sdk"; [ -d "$SDK" ] || SDK="$DEV/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk"

rm -rf "$APP" "$OUT/Pad-Deck-Helper-Mac.zip"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources" "$OUT/build"
cp helper/icon/AppIcon.icns "$APP/Contents/Resources/AppIcon.icns"
for arch in arm64 x86_64; do
  "$SWIFTC" -O -swift-version 5 -sdk "$SDK" -target "$arch-apple-macos13.0" \
    -o "$OUT/build/PadDeckHelper-$arch" helper/PadDeckHelper.swift
done
lipo -create -output "$APP/Contents/MacOS/PadDeckHelper" "$OUT/build/PadDeckHelper-arm64" "$OUT/build/PadDeckHelper-x86_64"
rm -rf "$OUT/build"

cat > "$APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleIdentifier</key><string>io.github.fedorsosnin.pad-deck-helper</string>
  <key>CFBundleName</key><string>Pad Deck Helper</string>
  <key>CFBundleDisplayName</key><string>Pad Deck Helper</string>
  <key>CFBundleExecutable</key><string>PadDeckHelper</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>LSUIElement</key><true/>
  <key>NSLocalNetworkUsageDescription</key><string>Pad Deck keys on your network ask Pad Deck Helper to open websites and apps on this Mac.</string>
</dict>
</plist>
EOF
codesign --force --sign - "$APP"
ditto -c -k --keepParent "$APP" "$OUT/Pad-Deck-Helper-Mac.zip"
echo "Built $APP and $OUT/Pad-Deck-Helper-Mac.zip"
