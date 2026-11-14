#!/bin/bash
# Installs the Pad Deck Helper on a Mac and starts it at every login. One line:
#
#   curl -fsSL https://raw.githubusercontent.com/FedorSosnin/pad-deck/main/helper/install-helper.sh | bash
#
# It prints the pairing code to enter on the Pad (Menu → Helper). Run it again to update.
# Remove:  launchctl bootout gui/$(id -u)/io.github.fedorsosnin.pad-deck-helper; rm -rf ~/Library/Application\ Support/Pad\ Deck\ Helper ~/Library/LaunchAgents/io.github.fedorsosnin.pad-deck-helper.plist
set -euo pipefail
LABEL="io.github.fedorsosnin.pad-deck-helper"
DIR="$HOME/Library/Application Support/Pad Deck Helper"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"

[ "$(uname)" = Darwin ] || { echo "The Pad Deck Helper installer is for macOS."; exit 1; }
PY=$(command -v python3) || { echo "Needs python3 (install Apple's Command Line Tools: xcode-select --install)."; exit 1; }

mkdir -p "$DIR" "$HOME/Library/LaunchAgents"
if [ -f "$SRC/pad-deck-helper.py" ]; then
  cp "$SRC/pad-deck-helper.py" "$DIR/"
else
  curl -fsSL -o "$DIR/pad-deck-helper.py" https://raw.githubusercontent.com/FedorSosnin/pad-deck/main/helper/pad-deck-helper.py
fi

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>$PY</string><string>$DIR/pad-deck-helper.py</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$DIR/helper.log</string>
  <key>StandardErrorPath</key><string>$DIR/helper.log</string>
</dict>
</plist>
EOF

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
sleep 1
CODE=$("$PY" "$DIR/pad-deck-helper.py" --code)
echo
echo "Pad Deck Helper is running and starts at login."
echo "On the Pad: Menu → Helper, enter this computer ($(scutil --get LocalHostName).local) and the pairing code: $CODE"
echo "(If macOS asks whether python3 may accept incoming connections, click Allow.)"
