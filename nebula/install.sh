#!/bin/sh
# Installs Pad Deck Lite on a rooted Creality Nebula Pad (run there as root from /usr/data/pad-deck).
# Switches Creality's screen UI off (it stays installed; uninstall.sh switches it back on).
set -e
D=/usr/data/pad-deck
cd $D
find $D -name '._*' -exec rm -f {} +           # macOS metadata files from older bundles
[ -f app/layout.json ] || echo '{"columns": 4, "rows": 2, "pages": [{"buttons": []}]}' > app/layout.json
cp S99zpad_deck /etc/init.d/S99zpad_deck
chmod +x /etc/init.d/S99zpad_deck
if [ -x /etc/init.d/S99start_app ]; then
    /etc/init.d/S99start_app stop >/dev/null 2>&1 || true
    chmod a-x /etc/init.d/S99start_app          # Creality's UI (and its watchdog) no longer start at boot
fi
/etc/init.d/S99zpad_deck restart
echo "Pad Deck Lite installed."
