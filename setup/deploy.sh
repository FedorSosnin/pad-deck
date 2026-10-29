#!/bin/bash
# Copies the deck app from this Mac to the Pad and reloads the screen.
# The Pad's layout.json, settings.json and its icons are left alone: the Pad is the source of truth for them.
set -euo pipefail
cd "$(dirname "$0")/.."
KEY="$HOME/.ssh/pad-deck"
PAD="deck@pad-deck.local"

rsync -a --exclude .claude --exclude setup --exclude layout.json --exclude layouts --exclude settings.json --exclude icons -e "ssh -i $KEY" ./ "$PAD:pad-deck/"
rsync -a --ignore-existing -e "ssh -i $KEY" icons/ "$PAD:pad-deck/icons/"   # add new icons, never overwrite
rsync -a -e "ssh -i $KEY" icons/deck-*.svg "$PAD:pad-deck/icons/"            # except the generated library (make-icons.py)
ssh -i "$KEY" "$PAD" 'sudo systemctl restart pad-deck-server pad-deck-kiosk'
echo "Deployed to the Pad."
