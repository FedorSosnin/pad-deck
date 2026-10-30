#!/bin/bash
# Copies Pad Deck Lite from this Mac to a rooted Nebula Pad and (re)installs it.
#   bash nebula/deploy-nebula.sh <nebula-ip>
# The Nebula downloads a bundle from a short-lived web server on this Mac, so only its root login is needed
# (password "creality", typed for you). Its layout.json and settings.json are left alone.
set -euo pipefail
cd "$(dirname "$0")/.."
NEB="${1:?Usage: deploy-nebula.sh <nebula-ip>}"
PORT=8765
MAC=$(route -n get "$NEB" 2>/dev/null | awk '/interface:/{print $2}' | xargs ipconfig getifaddr)

STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"; [ -n "${HTTP:-}" ] && kill $HTTP 2>/dev/null' EXIT
mkdir -p "$STAGE/pad-deck/app" "$STAGE/pad-deck/lite"
rsync -a --exclude .git --exclude .gitignore --exclude .claude --exclude docs --exclude setup --exclude nebula \
  --exclude layouts --exclude layout.json --exclude settings.json --exclude build-image.sh --exclude README.md \
  --exclude __pycache__ --exclude '*.img' ./ "$STAGE/pad-deck/app/"
cp nebula/deck_lite.py "$STAGE/pad-deck/lite/"
cp -r nebula/assets "$STAGE/pad-deck/lite/assets"
cp nebula/S99zpad_deck nebula/install.sh nebula/uninstall.sh "$STAGE/pad-deck/"
COPYFILE_DISABLE=1 tar -czf "$STAGE/pad-deck.tgz" -C "$STAGE" pad-deck   # no macOS ._ metadata files

python3 -m http.server $PORT --bind "$MAC" --directory "$STAGE" >/dev/null 2>&1 &
HTTP=$!
sleep 1
expect nebula/nebula-ssh.exp "$NEB" "cd /usr/data && echo Downloading... && wget -q -O /tmp/pad-deck.tgz http://$MAC:$PORT/pad-deck.tgz && echo Unpacking... && gunzip -c /tmp/pad-deck.tgz | tar -x && rm /tmp/pad-deck.tgz && sh pad-deck/install.sh"
echo "Deployed to $NEB. Editor: http://$NEB:8099"
